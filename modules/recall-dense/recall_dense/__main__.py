"""Dense recall worker (D-61): keeps an embedding per claim with a text value in schema `recall_dense`.
Needs no runner; needs pgvector in Postgres and fastembed here (pip install -e '.[dense]').

A pass embeds the claims recorded since the last pass that this module can read and that have a text
value (the literal; claims about entities alone are left to BM25), with kernel_mcp.recall.MODEL, and
drops the claims redacted since the last pass. It wakes on applied change sets and on redactions, and
sweeps every KK_SWEEP_SECONDS. The index is derived: delete its rows and the next pass rebuilds it.

Runs inside `kk serve` (part `dense`), or alone: python -m recall_dense.
Env: DATABASE_URL (a login that may SET ROLE to KK_ROLE), KK_ROLE (default kkm_recall_dense),
KK_SWEEP_SECONDS (default 30), KK_EMBED_CACHE (where the model is kept), KK_DENSE_BATCH (claims per transaction, default 256).
"""
import os
import sys

from kernel_mcp.recall import MODEL, embedder
from kk.lib import connect, listen

NEW = """select c.id, c.scope, c.object_value->>'@value' from api.claims c
         where c.redacted_at is null and length(c.object_value->>'@value') > 0
           and (%(since)s::timestamptz is null or c.recorded_at >= %(since)s)
           and not exists (select 1 from recall_dense.vec v where v.claim_id = c.id)
         order by c.recorded_at limit %(n)s"""
FORGET = """delete from recall_dense.vec v using api.claims c
            where c.id = v.claim_id and c.redacted_at is not null
              and (%(since)s::timestamptz is null or c.redacted_at >= %(since)s)"""


class Pass:
    def __init__(self, model):
        self.model = model

    def __call__(self, conn) -> None:
        state = conn.execute("select model, recorded_at, redacted_at from recall_dense.state").fetchone()
        if state and state[0] != MODEL:
            sys.exit(f"recall-dense: the index was built with {state[0]}, not {MODEL}; delete its rows to rebuild")
        since, redacted_since = (state or (None, None, None))[1:]
        # Claims recorded in a transaction still open now can carry an earlier recorded_at: look back.
        now = conn.execute("select now() - interval '5 minutes'").fetchone()[0]
        added = 0
        while True:
            with conn.transaction():
                rows = conn.execute(NEW, {"since": since, "n": int(os.environ.get("KK_DENSE_BATCH", "256"))}).fetchall()
                if not rows:
                    break
                vecs = self.model.embed([r[2] for r in rows])
                with conn.cursor() as cur:
                    cur.executemany("insert into recall_dense.vec(claim_id, scope, embedding) values (%s, %s, %s::vector)"
                                    " on conflict (claim_id) do nothing",
                                    [(r[0], r[1], "[" + ",".join(f"{x:.6f}" for x in v) + "]") for r, v in zip(rows, vecs)])
                added += len(rows)
        with conn.transaction():
            dropped = conn.execute(FORGET, {"since": redacted_since}).rowcount
            conn.execute("""insert into recall_dense.state(id, model, recorded_at, redacted_at) values (true, %s, %s, %s)
                            on conflict (id) do update set recorded_at = excluded.recorded_at,
                                                           redacted_at = excluded.redacted_at""", (MODEL, now, now))
        if added or dropped:
            print(f"recall-dense: {added} claims embedded, {dropped} redacted dropped", flush=True)


def main() -> None:
    model = embedder(MODEL, download=True)
    if model is None:
        sys.exit("recall-dense: no embedding model (pip install -e '.[dense]'; the first start downloads it)")
    with connect(os.environ.get("KK_ROLE", "kkm_recall_dense")) as conn:
        if conn.execute("select to_regclass('recall_dense.vec')").fetchone()[0] is None:
            sys.exit("recall-dense: not installed (its migration needs pgvector)")
        conn.execute("listen kernel_redaction")
        print(f"recall-dense: {MODEL}; listening on kernel_change_set and kernel_redaction", flush=True)
        listen(conn, Pass(model))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

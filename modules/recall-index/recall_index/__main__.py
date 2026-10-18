"""Recall index worker (D-60): keeps the BM25 index in schema `recall` up to date. Needs no runner.

A pass indexes the claims recorded since the last pass that this module can read (their text: the
literal value plus the words of the subject IRI, as recall has always searched), updates the term and
corpus statistics per scope, and drops the claims redacted since the last pass. Status changes need no
pass: deprecated claims are left out at query time. It wakes on applied change sets and on redactions,
and sweeps every KK_SWEEP_SECONDS (NOTIFY is not durable). The index is derived: drop schema `recall`'s
rows and the next pass rebuilds it.

Runs inside `kk serve` when installed, or alone: python -m recall_index.
Env: DATABASE_URL (a login that may SET ROLE to KK_ROLE), KK_ROLE (default kkm_recall_index),
KK_SWEEP_SECONDS (default 30).
"""
import os
import sys

from kernel_mcp.recall import DOC  # a claim's searchable text, one definition for both
from kk.lib import connect, listen

# One statement per pass: new rows, then their terms and corpus counts, in one transaction.
INDEX = f"""
with new as (
  insert into recall.doc(claim_id, scope, len, tsv)
  select c.id, c.scope, (select coalesce(sum(coalesce(array_length(u.positions, 1), 1)), 0)
                         from unnest({DOC}) u), {DOC}
  from api.claims c
  where c.redacted_at is null
    and (%(since)s::timestamptz is null or c.recorded_at >= %(since)s)
  on conflict (claim_id) do nothing
  returning claim_id, scope, len, tsv),
terms as (
  insert into recall.term(scope, lexeme, df)
  select n.scope, u.lexeme, count(*) from new n, unnest(n.tsv) u group by 1, 2
  on conflict (scope, lexeme) do update set df = recall.term.df + excluded.df
  returning 1),
corpus as (
  insert into recall.corpus(scope, n, total_len)
  select scope, count(*), sum(len) from new group by scope
  on conflict (scope) do update set n = recall.corpus.n + excluded.n, total_len = recall.corpus.total_len + excluded.total_len
  returning 1)
select (select count(*) from new), (select count(*) from terms), (select count(*) from corpus)"""

FORGET = """
with gone as (
  delete from recall.doc d using api.claims c
  where c.id = d.claim_id and c.redacted_at is not null
    and (%(since)s::timestamptz is null or c.redacted_at >= %(since)s)
  returning d.scope, d.len, d.tsv),
terms as (
  update recall.term t set df = t.df - g.n
  from (select scope, u.lexeme, count(*) n from gone, unnest(gone.tsv) u group by 1, 2) g
  where t.scope = g.scope and t.lexeme = g.lexeme returning 1),
corpus as (
  update recall.corpus k set n = k.n - g.n, total_len = k.total_len - g.len
  from (select scope, count(*) n, sum(len) len from gone group by scope) g
  where k.scope = g.scope returning 1)
select count(*) from gone"""


class Pass:
    def __call__(self, conn) -> None:
        with conn.transaction():
            state = conn.execute("select recorded_at, redacted_at from recall.state").fetchone()
            since, redacted_since = state or (None, None)
            # Claims recorded in a transaction still open now can carry an earlier recorded_at: look
            # back a little; `on conflict do nothing` makes the overlap free.
            now = conn.execute("select now() - interval '5 minutes'").fetchone()[0]
            added = conn.execute(INDEX, {"since": since}).fetchone()[0]
            dropped = conn.execute(FORGET, {"since": redacted_since}).fetchone()[0]
            conn.execute("""insert into recall.state(id, recorded_at, redacted_at) values (true, %s, %s)
                            on conflict (id) do update set recorded_at = excluded.recorded_at,
                                                           redacted_at = excluded.redacted_at""", (now, now))
        if added or dropped:
            print(f"recall-index: {added} claims indexed, {dropped} redacted dropped", flush=True)


def main() -> None:
    with connect(os.environ.get("KK_ROLE", "kkm_recall_index")) as conn:
        conn.execute("listen kernel_redaction")
        print("recall-index: listening on kernel_change_set and kernel_redaction", flush=True)
        listen(conn, Pass())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

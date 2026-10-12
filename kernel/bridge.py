"""The outbox bridge: delivers outbox rows to the chains modules subscribe (docs/CORE.md).

RuleGo has no Postgres LISTEN endpoint (docs/SPIKE_NOTES.md), so this process LISTENs on
`outbox` and, on start, on each notification and every `poll` seconds, works through pending
rows whose retry time has come, oldest first, locking each (SKIP LOCKED, so bridges can run
side by side). A row goes to every chain whose activation matches it (label, op, `where`
equalities on the row's properties), with `causation` = outbox:<seq> and `depth` as metadata.

Outcomes: `done` when every matching chain succeeded (or none matched); `stopped` when a
chain failed on the loop guard (never retried); `dead` after max_attempts chain failures.
A failure backs off (1, 2, 4 ... 60 s). RuleGo being unreachable is not a chain failure: the
row waits. Delivery is at-least-once; chains that already succeeded for a row are skipped on
retry.

Run: python -m kernel.bridge   (DATABASE_URL, KERNEL_GRAPH, RULEGO_URL; POLL seconds)
"""
import json
import os
import sys
import threading

import psycopg
import requests
from psycopg.types.json import Jsonb

from kernel import db
from kernel.rulego import RuleGo

GUARD = "kernel loop guard"
ROW_KEYS = ("seq", "graph", "label", "op", "element_id", "old_props", "new_props", "causation", "depth",
            "attempts", "delivered")


def routes(conn) -> list[dict]:
    """Activations of the held modules."""
    rows = conn.execute("MATCH (m:module) WHERE m.retired_at IS NULL RETURN m.activations").fetchall()
    return [a for (acts,) in rows for a in (acts or [])]


def matches(a: dict, row: dict) -> bool:
    if a.get("label") != row["label"] or row["op"] not in a.get("ops", ("INSERT", "UPDATE", "DELETE")):
        return False
    props = row["new_props"] if row["op"] != "DELETE" else row["old_props"]
    return all((props or {}).get(k) == v for k, v in (a.get("where") or {}).items())


def deliver_one(conn, rulego: RuleGo, acts: list[dict], max_attempts: int) -> bool | None:
    """Handle one due row. True: progress made; False: nothing due; None: RuleGo unreachable."""
    with conn.transaction():
        r = conn.execute(f"""SELECT {', '.join(ROW_KEYS)} FROM kernel.outbox
                             WHERE status = 'pending' AND next_try_at <= clock_timestamp() AND graph = %s
                             ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED""", (db.graph_name(),)).fetchone()
        if r is None:
            return False
        row = dict(zip(ROW_KEYS, r))
        msg = {k: row[k] for k in ROW_KEYS[:9]}
        delivered, results = list(row["delivered"]), {}
        for chain in [a["chain"] for a in acts if matches(a, row)]:
            if chain in delivered:
                continue
            try:
                resp = requests.post(f"{rulego.url}/api/v1/rules/{chain}/execute/GRAPH_CHANGE",
                                     params={"causation": f"outbox:{row['seq']}", "depth": row["depth"]},
                                     json=msg, timeout=rulego.timeout)
            except requests.RequestException:
                _save(conn, row, delivered, results)
                return None
            if resp.status_code == 200:
                delivered.append(chain)
                results[chain] = _json(resp.text)
                continue
            error = f"{chain}: HTTP {resp.status_code}: {resp.text}"[:4000]
            attempts = row["attempts"] + 1
            status = "stopped" if GUARD in resp.text else ("dead" if attempts >= max_attempts else "pending")
            conn.execute("""UPDATE kernel.outbox SET status = %s, attempts = %s, last_error = %s,
                                delivered = %s, results = results || %s,
                                next_try_at = clock_timestamp() + make_interval(secs => least(60, 2 ^ (%s - 1))),
                                done_at = CASE WHEN %s <> 'pending' THEN clock_timestamp() END
                            WHERE seq = %s""",
                         (status, attempts, error, Jsonb(delivered), Jsonb(results), attempts, status, row["seq"]))
            _log(row["seq"], status, chain=chain, error=resp.text[:300])
            return True
        conn.execute("""UPDATE kernel.outbox SET status = 'done', delivered = %s, results = results || %s,
                            done_at = clock_timestamp() WHERE seq = %s""",
                     (Jsonb(delivered), Jsonb(results), row["seq"]))
        _log(row["seq"], "done", chains=delivered)
        return True


def drain(conn, rulego: RuleGo) -> bool:
    """Deliver every due row. False if RuleGo was unreachable."""
    acts = routes(conn)
    conn.commit()
    max_attempts = conn.execute("SELECT kernel.limit_of('max_attempts')").fetchone()[0]
    conn.commit()
    while True:
        res = deliver_one(conn, rulego, acts, max_attempts)
        if res is None:
            return False
        if not res:
            return True


def run(poll: float = 5.0, stop: threading.Event | None = None, rulego: RuleGo | None = None):
    stop = stop or threading.Event()
    rulego = rulego or RuleGo()
    listen = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    work = db.connect()
    listen.execute("LISTEN outbox")
    _log(None, "up", graph=db.graph_name(), rulego=rulego.url)
    try:
        while not stop.is_set():
            ok = drain(work, rulego)
            wait = poll if ok else min(poll, 1.0)
            for _ in listen.notifies(timeout=wait, stop_after=1):
                pass
    finally:
        listen.close()
        work.close()


def _save(conn, row, delivered, results):
    if delivered != list(row["delivered"]):
        conn.execute("UPDATE kernel.outbox SET delivered = %s, results = results || %s WHERE seq = %s",
                     (Jsonb(delivered), Jsonb(results), row["seq"]))


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _log(seq, status, **kw):
    print(json.dumps({"seq": seq, "status": status, **kw}), flush=True)


if __name__ == "__main__":
    try:
        run(float(os.environ.get("POLL", "5")))
    except KeyboardInterrupt:
        sys.exit(0)

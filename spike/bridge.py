"""S0 outbox bridge: hands outbox rows to a RuleGo chain and marks them done.

RuleGo has no Postgres LISTEN endpoint, so this process LISTENs on channel `outbox`, and
on start, on every notification and every POLL seconds drains pending rows: lock the oldest
undone row (SKIP LOCKED, so several bridges can run), POST it to the chain's synchronous
execute API, and set done_at in the same transaction. A row whose chain fails stays undone,
records the error and is retried. Delivery is at-least-once: a crash between the chain
finishing and the commit re-delivers the row, so chain steps must be idempotent.

Env: DATABASE_URL, RULEGO_URL (default http://127.0.0.1:9090), CHAIN (default s0_on_change),
POLL seconds (default 5).
"""
import json
import os
import sys
import time

import psycopg
import requests

RULEGO = os.environ.get("RULEGO_URL", "http://127.0.0.1:9090")
CHAIN = os.environ.get("CHAIN", "s0_on_change")
POLL = float(os.environ.get("POLL", "5"))


def deliver_one(conn):
    """Deliver the oldest undone row. Returns True if a row was handled successfully."""
    with conn.transaction():
        row = conn.execute(
            """SELECT seq, graph, label, op, element_id, old_props, new_props, causation, depth
                 FROM public.outbox WHERE done_at IS NULL
                ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if row is None:
            return False
        keys = ("seq", "graph", "label", "op", "element_id", "old_props", "new_props", "causation", "depth")
        msg = dict(zip(keys, row))
        try:
            r = requests.post(f"{RULEGO}/api/v1/rules/{CHAIN}/execute/GRAPH_CHANGE",
                              params={"causation": f"outbox:{msg['seq']}"}, json=msg, timeout=60)
            ok, result = r.status_code == 200, r.text
        except requests.RequestException as e:
            ok, result = False, repr(e)
        if ok:
            conn.execute("UPDATE public.outbox SET done_at = clock_timestamp(), result = %s WHERE seq = %s",
                         (result, msg["seq"]))
        else:
            conn.execute("UPDATE public.outbox SET attempts = attempts + 1, last_error = %s WHERE seq = %s",
                         (result[:2000], msg["seq"]))
        print(json.dumps({"seq": msg["seq"], "ok": ok, "result": result[:300]}), flush=True)
        return ok


def drain(conn):
    while deliver_one(conn):
        pass


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as listen, \
         psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as work:
        listen.execute("LISTEN outbox")
        print(json.dumps({"bridge": "up", "chain": CHAIN}), flush=True)
        while True:
            drain(work)  # catch-up on start, then after each wake-up
            for _ in listen.notifies(timeout=POLL, stop_after=1):
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

"""Review lane for people.

  kk review list
  kk review show <change set id>
  kk review approve <id>... [--note TEXT]
  kk review approve --all [--from AGENT_IRI] [--note TEXT]
  kk review reject <id>... --note TEXT

Env: KK_DATABASE_URL (or DATABASE_URL), the reviewer's own login (`kk person add NAME`). The kernel
refuses self-approval (KK025) and approvals outside the reviewer's cleared scopes.
"""
import argparse
import json
import os
import sys

import psycopg


def connect():
    url = os.environ.get("KK_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set KK_DATABASE_URL (or DATABASE_URL) to your own login")
    return psycopg.connect(url, autocommit=True)


def summary(row):
    preds = {}
    for op in row["ops"]:
        preds[op.get("predicate", op["op"])] = preds.get(op.get("predicate", op["op"]), 0) + 1
    return f"{row['id']}  {row['attributed_to']}  {row['target_scope']}  {len(row['ops'])} ops  {preds}"


def main(argv=None):
    p = argparse.ArgumentParser(prog="kk review", description="Review lane")
    p.add_argument("action", choices=["list", "show", "approve", "reject"])
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--from", dest="agent")
    p.add_argument("--note")
    a = p.parse_args(argv)
    conn = connect()
    queue = conn.execute("select coalesce(jsonb_agg(to_jsonb(q) order by submitted_at), '[]') from api.review_queue q"
                         ).fetchone()[0]
    if a.action == "list":
        for row in queue:
            print(summary(row))
        print(f"{len(queue)} awaiting review")
        return
    if a.action == "show":
        for row in queue:
            if row["id"] in a.ids:
                print(json.dumps(row, indent=2))
        return
    ids = [r["id"] for r in queue if not a.agent or r["attributed_to"] == a.agent] if a.all else a.ids
    if a.action == "reject" and not a.note:
        sys.exit("reject needs --note")
    for cs_id in ids:
        try:
            status = conn.execute("select api.review(%s::uuid, %s, %s)", (cs_id, a.action == "approve", a.note)).fetchone()[0]
        except psycopg.Error as e:
            sys.exit(f"{cs_id}: {e.diag.message_primary or e}")
        print(cs_id, status)


if __name__ == "__main__":
    main()

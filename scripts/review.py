"""Review lane for people (stdlib only).

  python scripts/review.py list
  python scripts/review.py show <change set id>
  python scripts/review.py approve <id>... [--note TEXT]
  python scripts/review.py approve --all [--from AGENT_IRI] [--note TEXT]
  python scripts/review.py reject <id>... --note TEXT

Env: KK_URL (default http://127.0.0.1:8080/api), KK_TOKEN (a reviewer's JWT, e.g. person_owner).
The kernel refuses self-approval (KK025) and approvals outside the reviewer's cleared scopes.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("KK_URL", "http://127.0.0.1:8080/api")


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['KK_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def summary(row):
    preds = {}
    for op in row["ops"]:
        preds[op.get("predicate", op["op"])] = preds.get(op.get("predicate", op["op"]), 0) + 1
    return f"{row['id']}  {row['attributed_to']}  {row['target_scope']}  {len(row['ops'])} ops  {preds}"


def main():
    p = argparse.ArgumentParser(description="Review lane")
    p.add_argument("action", choices=["list", "show", "approve", "reject"])
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--from", dest="agent")
    p.add_argument("--note")
    a = p.parse_args()
    queue = call("GET", "/review_queue?order=submitted_at")
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
        status = call("POST", "/rpc/review", {"change_set_id": cs_id, "approve": a.action == "approve", "note": a.note})
        print(cs_id, status)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Deploy rule chains to RuleGo-Server over its REST API (hot; no restart).

Usage: rulego_deploy.py CHAIN.json|DIR [...]   (RULEGO_URL, default http://127.0.0.1:9090)
Each file's ruleChain.id is the chain id. Prints {"deployed": [ids]}.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

URL = os.environ.get("RULEGO_URL", "http://127.0.0.1:9090")


def deploy(path: Path) -> str:
    chain = json.loads(path.read_text())
    cid = chain["ruleChain"]["id"]
    req = urllib.request.Request(f"{URL}/api/v1/rules/{cid}", data=json.dumps(chain).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"{cid}: HTTP {r.status} {r.read()[:200]!r}")
    return cid


def main(args):
    files = [f for a in map(Path, args) for f in (sorted(a.glob("*.json")) if a.is_dir() else [a])]
    print(json.dumps({"deployed": [deploy(f) for f in files]}))


if __name__ == "__main__":
    main(sys.argv[1:])

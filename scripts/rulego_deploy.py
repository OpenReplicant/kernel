#!/usr/bin/env python3
"""Deploy rule chains to RuleGo-Server over its REST API (hot; no restart).

Usage: rulego_deploy.py CHAIN.json|DIR [...]   (RULEGO_URL, default http://127.0.0.1:9090)
Prints {"deployed": [chain ids]}.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pclib.rulego import RuleGo  # noqa: E402

if __name__ == "__main__":
    print(json.dumps({"deployed": RuleGo().deploy_paths(*sys.argv[1:])}))

"""Evaluator: eval run files into the kernel as DQV measurements (spec §12, D-71). A plain command.

  python -m evaluator --suite NAME [--artifacts DIR] RUN.json...

Each file is stored as an artifact and recorded (measure.py) in one change set keyed by the suite, the
run's name and the file's hash: the same file again is a no-op, and a changed file supersedes the run's
earlier scores. Prints one line per file.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_evaluator), KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kk.changes import prior as prior_claims, supersede
from kk.lib import connect, run, submit

from .measure import measurements, slug

MODULE = "urn:kk:module:evaluator"
AGENT = "urn:kk:agent:evaluator"
SCOPE = "urn:kk:scope:default"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record eval run files as DQV measurements.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--suite", required=True, help="the eval suite, e.g. scifact")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    a = ap.parse_args(argv)
    store = Path(a.artifacts)
    store.mkdir(parents=True, exist_ok=True)
    with connect(os.environ.get("KK_ROLE", "kkm_evaluator")) as conn, conn.cursor() as cur:
        for f in map(Path, a.files):
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if not (store / sha).exists():
                (store / sha).write_bytes(data)
            ops = measurements(json.loads(data), a.suite, f.stem, sha)
            prefix = f"eval:{slug(a.suite)}:{slug(f.stem)}|"
            cs = {"idempotency_key": prefix + sha[:16], "attributed_to": AGENT, "target_scope": SCOPE,
                  "ops": [{"op": "add_artifact", "sha256": sha, "media_type": "application/json", "byte_size": len(data),
                           "location": f"file://{store / sha}", "source_uri": f"file:{f.name}",
                           "retrieved_at": datetime.now(timezone.utc).isoformat()}, *ops]}
            with run(cur, MODULE, {"suite": a.suite, "file": f.name}) as (run_id, outputs):
                prior = prior_claims(cur, AGENT, prefix, cs["idempotency_key"])
                if prior:
                    supersede(cs, prior)
                res = submit(cur, cs, run_id)
                outputs.update(measurements=len(ops) - 1, superseded=len(prior))
            print(json.dumps({"file": f.name, "measurements": len(ops) - 1, "superseded": len(prior),
                              "change_set": str(res["id"]), "status": res["status"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

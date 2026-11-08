"""encoder-morphogen: Morphogen's verdicts into the kernel (D-99). A plain command.

  python -m encoder_morphogen --project NAME [--artifacts DIR] EVIDENCE.db

The log's hash chain is checked first; a broken chain fails the run (recorded as a failed run) and records
nothing. Each verdict the judge wrote is stored as an artifact (its canonical JSON) and recorded (encode.py) in
one change set keyed by the project, its sequence number and its hash, so running again submits nothing new.
Prints one line per verdict recorded.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_morphogen), KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kk.lib import connect, run, submit

from .encode import ChainBroken, canonical, claims, read_log, slug

MODULE = "urn:kk:module:encoder-morphogen"
AGENT = "urn:kk:agent:encoder-morphogen"
SCOPE = "urn:kk:scope:default"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record Morphogen's verdicts as DQV measurements.")
    ap.add_argument("log", help="Morphogen's evidence log (<git common dir>/morphogen/evidence.db)")
    ap.add_argument("--project", required=True, help="the project the log belongs to, e.g. substrate")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    a = ap.parse_args(argv)
    store = Path(a.artifacts)
    store.mkdir(parents=True, exist_ok=True)
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_morphogen")) as conn, conn.cursor() as cur:
        try:
            with run(cur, MODULE, {"project": a.project, "log": str(a.log)}) as (_, outputs):
                try:
                    records = read_log(Path(a.log))
                except ChainBroken as e:
                    outputs.update(refused=str(e), broken_at=e.seq)   # kept with the failed run
                    raise
                outputs.update(records=len(records))
        except ChainBroken as e:
            print(json.dumps({"refused": str(e)}))
            return 1
        verdicts = [r for r in records if r.get("kind") == "verdict" and r.get("by") == "judge"]
        new = 0
        for rec in verdicts:
            data = canonical(rec).encode()
            sha = hashlib.sha256(data).hexdigest()
            if not (store / sha).exists():
                (store / sha).write_bytes(data)
            cs = {"idempotency_key": f"morphogen:{slug(a.project)}:{rec['seq']}|{rec['hash'][:16]}",
                  "attributed_to": AGENT, "target_scope": SCOPE,
                  "ops": [{"op": "add_artifact", "sha256": sha, "media_type": "application/json",
                           "byte_size": len(data), "location": f"file://{store / sha}",
                           "source_uri": f"file:{Path(a.log).name}#seq={rec['seq']}",
                           "retrieved_at": datetime.now(timezone.utc).isoformat()},
                          *claims(rec, a.project, sha)]}
            cur.execute("select exists (select 1 from api.change_sets where idempotency_key = %s)",
                        (cs["idempotency_key"],))
            if cur.fetchone()[0]:   # recorded before: the kernel would answer with the same change set
                continue
            with run(cur, MODULE, {"project": a.project, "seq": rec["seq"]}) as (run_id, outputs):
                res = submit(cur, cs, run_id)
                outputs.update(claims=len(cs["ops"]) - 1, status=res["status"])
            new += 1
            print(json.dumps({"verdict": f"urn:kk:verdict:{slug(a.project)}:{rec['seq']}", "outcome": rec.get("verdict"),
                              "claims": len(cs["ops"]) - 1, "change_set": str(res["id"]), "status": res["status"]}))
        print(json.dumps({"project": a.project, "verdicts": len(verdicts), "new": new, "records": len(records)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""BPMN encoder: a BPMN 2.0 XML model into the kernel as the documented process. A plain command (D-41).

  python -m bpmn_encoder --process IRI [--bpmn-process ID] [--artifacts DIR] [--no-filter] MODEL.bpmn

Runs the ingest filter (fail closed), stores the model as an artifact, encodes it into one change set
(encode.py) and supersedes this encoder's claims from earlier versions of the same file for the same
process (kk.changes). The same model again is a no-op.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_bpmn), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
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
from kk.lib import connect, ingest_filter, run, submit

from .encode import encode

MODULE = "urn:kk:module:encoder-bpmn"
AGENT = "urn:kk:agent:encoder-bpmn"
SCOPE = "urn:kk:scope:default"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Encode a BPMN 2.0 model into the documented process.")
    ap.add_argument("model")
    ap.add_argument("--process", required=True, help="the process IRI, e.g. urn:kk:process:order-to-cash")
    ap.add_argument("--bpmn-process", help="the BPMN process id, when the file has more than one")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    model = Path(a.model).resolve()
    if not a.no_filter:
        found = ingest_filter(model.parent, [model.name])
        if found["blocked"]:
            print(json.dumps({"blocked": found["blocked"]}))
            return 1
    data = model.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    ops = encode(data, a.process, sha, a.bpmn_process)
    artifacts = Path(a.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    if not (artifacts / sha).exists():
        (artifacts / sha).write_bytes(data)
    prefix = f"bpmn:{a.process}|{model.name}|"
    cs = {"idempotency_key": f"{prefix}{sha[:16]}", "attributed_to": AGENT, "target_scope": SCOPE,
          "ops": [{"op": "add_artifact", "sha256": sha, "media_type": "application/bpmn+xml", "byte_size": len(data),
                   "location": f"file://{artifacts / sha}", "source_uri": f"file:{model.name}",
                   "retrieved_at": datetime.now(timezone.utc).isoformat()}, *ops]}
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_bpmn")) as conn, conn.cursor() as cur:
        prior = prior_claims(cur, AGENT, prefix, cs["idempotency_key"])
        if prior:
            supersede(cs, prior)
        with run(cur, MODULE, {"model": model.name, "process": a.process}) as (run_id, outputs):
            res = submit(cur, cs, run_id)
            outputs.update(claims=len(ops))
    print(json.dumps({"change_set": str(res["id"]), "status": res["status"], "claims": len(ops),
                      "superseded": len(prior)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Paper encoder: scholarly papers (JSON lines) into the kernel. A plain command (D-41, D-59).

  python -m paper_encoder --source NAME [--batch N] [--kind ResearchPaper] [--artifacts DIR] [--no-filter] FILE

Runs the ingest filter (fail closed): a paper (line) with a finding is left out and reported, never
stored, since papers are independent of each other; a finding without a line holds back the whole file.
Stores the file (without such lines) as an artifact and encodes it (encode.py) into
change sets of --batch papers (default 500), each keyed by the file's hash and its position, so the same
file again is a no-op. Prints the change set ids.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_papers), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kk.lib import connect, ingest_filter, run, submit

from .encode import encode, read

MODULE = "urn:kk:module:encoder-papers"
AGENT = "urn:kk:agent:encoder-papers"
SCOPE = "urn:kk:scope:default"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Encode scholarly papers (JSON lines) into the kernel.")
    ap.add_argument("file")
    ap.add_argument("--source", required=True, help="a short name for the collection, e.g. scifact")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--kind", default="ResearchPaper", help="the FaBiO class (default ResearchPaper)")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    path = Path(a.file).resolve()
    data, skipped = path.read_bytes(), []
    if not a.no_filter:
        found = ingest_filter(path.parent, [path.name])
        lines = {b.get("line") for b in found["blocked"]}
        if None in lines:              # a finding without a line: the file as a whole is held back
            print(json.dumps({"blocked": found["blocked"]}))
            return 1
        if lines:                      # papers are independent: leave out the lines with findings, never store them
            rows = data.decode("utf-8").splitlines(keepends=True)
            skipped = [f"line {n}: {next(b['rule'] for b in found['blocked'] if b.get('line') == n)}" for n in sorted(lines)]
            data = "".join(r for n, r in enumerate(rows, 1) if n not in lines).encode()
    sha = hashlib.sha256(data).hexdigest()
    papers = read(data)
    artifacts = Path(a.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    if not (artifacts / sha).exists():
        (artifacts / sha).write_bytes(data)
    artifact = {"op": "add_artifact", "sha256": sha, "media_type": "application/jsonl", "byte_size": len(data),
                "location": f"file://{artifacts / sha}", "source_uri": f"file:{path.name}",
                "retrieved_at": datetime.now(timezone.utc).isoformat()}
    ids, claims = [], 0
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_papers")) as conn, conn.cursor() as cur, \
            run(cur, MODULE, {"file": path.name, "source": a.source, "papers": len(papers)}) as (run_id, outputs):
        for i in range(0, len(papers), a.batch):
            ops = encode(papers[i:i + a.batch], a.source, sha, a.kind)
            cs = {"idempotency_key": f"papers:{a.source}|{sha[:16]}|{i // a.batch}", "attributed_to": AGENT,
                  "target_scope": SCOPE, "ops": [artifact, *ops]}
            ids.append(str(submit(cur, cs, run_id)["id"]))
            claims += len(ops)
        outputs.update(papers=len(papers), claims=claims, change_sets=len(ids))
    print(json.dumps({"papers": len(papers), "claims": claims, "skipped": skipped, "change_sets": ids}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

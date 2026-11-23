"""encoder-backstage: Backstage catalog files into the kernel, in Backstage's terms (D-100). A plain command.

  python -m encoder_backstage --project NAME [--root DIR] [--artifacts DIR] catalog-info.yaml [...]

Each file becomes one change set (encode.py) keyed by the project, its path under --root and its content, so
reading an unchanged file again submits nothing. When a file changed, the change set also deprecates what this
encoder said from the file's earlier versions: a claim the new version states again is superseded by it, any
other is retracted (an entity removed from the catalog, a relation dropped); nothing is deleted. Findings (an
unknown kind, a ref to an entity the file does not define) are printed and kept with the run.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_backstage), KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from kk.lib import connect, run, submit

from .encode import AGENT, SCOPE, change_set, key_prefix, supersede

MODULE = "urn:kk:module:encoder-backstage"
PRIOR = """select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and s.idempotency_key <> %s"""


def encode_file(cur, path: Path, project: str, rel: str, store: Path) -> dict:
    cs, findings, counts = change_set(path, project, rel, store)
    cur.execute(PRIOR, (AGENT, key_prefix(project, rel), cs["idempotency_key"]))
    cols = [d.name for d in cur.description]
    prior = [dict(zip(cols, r)) for r in cur.fetchall()]
    if prior:
        supersede(cs, prior)
    cur.execute("select exists (select 1 from api.change_sets where idempotency_key = %s)", (cs["idempotency_key"],))
    known = cur.fetchone()[0]   # the kernel answers a repeat with the change set it already has
    with run(cur, MODULE, {"project": project, "file": rel}) as (run_id, outputs):
        res = submit(cur, cs, run_id)
        outputs.update(entities=counts, findings=findings, deprecated=len(prior))
    return {"file": rel, "entities": counts, "claims": sum(op["op"] == "add_claim" for op in cs["ops"]),
            "deprecated": len(prior), "findings": findings, "change_set": str(res["id"]), "status": res["status"],
            "new": not known}


PRUNE = """select c.id, c.status, s.idempotency_key
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated' and starts_with(s.idempotency_key, %s)"""


def prune(cur, project: str, kept: set[str]) -> dict | None:
    """Retract what this encoder said from the project's files that were not read this time (deleted or renamed):
    with --prune, the files given are the project's whole set. Nothing is deleted; the claims are retracted."""
    head = f"backstage:{project}:"
    cur.execute(PRUNE, (AGENT, head))
    gone = [(str(cid), st) for cid, st, key in cur.fetchall()
            if key[len(head):].rsplit(":", 1)[0] not in kept]
    if not gone:
        return None
    ids = sorted(c for c, _ in gone)
    cs = {"idempotency_key": f"{head}prune:{hashlib.sha256(' '.join(ids).encode()).hexdigest()[:16]}",
          "attributed_to": AGENT, "target_scope": SCOPE,
          "ops": [{"op": "set_status", "claim": c, "status": "deprecated", "expect_status": st, "reason": "retracted"} for c, st in gone]}
    with run(cur, MODULE, {"project": project, "prune": len(gone)}) as (run_id, outputs):
        res = submit(cur, cs, run_id)
        outputs.update(retracted=len(gone))
    return {"pruned": len(gone), "change_set": str(res["id"]), "status": res["status"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record Backstage catalog files as claims, in Backstage's terms.")
    ap.add_argument("files", nargs="+", help="catalog-info.yaml files")
    ap.add_argument("--project", required=True, help="whose catalog this is, e.g. substrate")
    ap.add_argument("--root", default=".", help="paths are recorded relative to this directory (default: .)")
    ap.add_argument("--prune", action="store_true", help="the files given are the project's whole set: retract what "
                    "earlier runs said from any other file (deleted or renamed)")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_backstage")) as conn, conn.cursor() as cur:
        kept: set[str] = set()
        for f in a.files:
            p = Path(f).resolve()
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else p.name
            kept.add(rel)
            print(json.dumps(encode_file(cur, p, a.project, rel, Path(a.artifacts))))
        if a.prune:
            res = prune(cur, a.project, kept)
            print(json.dumps(res or {"pruned": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

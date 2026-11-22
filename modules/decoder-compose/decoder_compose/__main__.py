"""decoder-compose: a service template's compose file, rendered from its version and the current claims (D-125).

  python -m decoder_compose --project NAME [--out compose.yaml] [--artifacts DIR]

Reads the template's current claims (`api.claims`, not deprecated) and the members' fields, takes the version the
template's `kkt:hasArtifact` names from the artifact store, and writes the compose file (stdout by default). Where
two current claims give one service two images, the later one wins and the other is reported. Reads only; writes no
claims.

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_decoder_compose), KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kk.lib import connect

from .decode import KKT, dump, render, template_iri

CLAIMS = """select c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers
            from api.claims c
            where c.status <> 'deprecated'
              and (c.subject = %(t)s or c.subject in (
                    select m.subject from api.claims m
                    where m.predicate = %(member)s and m.object_entity = %(t)s and m.status <> 'deprecated'))
            order by c.recorded_at"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a service template's compose file from the kernel.")
    ap.add_argument("--project", required=True, help="the compose project the encoder recorded, e.g. substrate")
    ap.add_argument("--out", default="-", help="where to write it (default: stdout)")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    a = ap.parse_args(argv)
    t = template_iri(a.project)
    with connect(os.environ.get("KK_ROLE", "kkm_decoder_compose")) as conn, conn.cursor() as cur:
        cur.execute(CLAIMS, {"t": t, "member": KKT + "memberOf"})
        cols = [d.name for d in cur.description]
        claims = [dict(zip(cols, r)) for r in cur.fetchall()]
    versions = [c["object_entity"] for c in claims if c["subject"] == t and c["predicate"] == KKT + "hasArtifact"]
    if not versions:
        print(json.dumps({"error": f"no current version of {t} (run encoder-compose first)"}), file=sys.stderr)
        return 1
    sha = versions[-1].removeprefix("urn:kk:artifact:")
    text = (Path(a.artifacts) / sha).read_text(encoding="utf-8")
    images = {}
    for c in claims:
        if (c["qualifiers"] or {}).get(KKT + "propertyName") == "image":
            images.setdefault(c["subject"], []).append((c["object_value"] or {}).get("@value"))
    for node, vals in images.items():
        if len(set(vals)) > 1:
            print(json.dumps({"finding": "two-images", "node": node, "images": vals, "used": vals[-1]}), file=sys.stderr)
    doc = render(text, claims, t)
    out = dump(doc)
    if a.out == "-":
        sys.stdout.write(out)
    else:
        Path(a.out).write_text(out, encoding="utf-8")
    print(json.dumps({"template": t, "version": sha, "services": len(doc["services"]), "out": a.out}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

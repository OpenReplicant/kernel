"""Map gap worker (M5-T4): lists what the organisation and process maps people describe still lack, as
questions an interviewing agent asks next. Needs no runner.

It wakes when an applied change set adds an org or process claim it reads (pattern triggers, D-79), plus a
sweep every KK_SWEEP_SECONDS. A pass reads the current map claims, finds the gaps (gaps.py) and submits one
change set per gap: an oa:Annotation (dcterms:type urn:kk:gap-kind:<kind>, oa:hasTarget the step or post,
oa:bodyValue the question), inferred and derived from the claims that put the target on the map. An
unchanged gap is a no-op; a gap that is closed has its claims retracted, so the list shrinks as the
person answers.

  python -m map_gaps [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_map_gaps), KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from kk.changes import prior as prior_claims, supersede
from kk.lib import connect, listen, manifest_patterns, run, submit

from .gaps import LABEL, PREDICATES, gaps

MODULE = "urn:kk:module:map-gaps"
AGENT = "urn:kk:agent:map-gaps"
SCOPE = "urn:kk:scope:default"
OA = "http://www.w3.org/ns/oa#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
CLAIMS = """select id, subject, predicate, object_entity, object_value, evidence_type from api.claims
            where status <> 'deprecated' and predicate = any(%s) and predicate <> %s
              and (predicate <> %s or object_entity like 'http://www.w3.org/ns/org#%%')"""
LABELS = """select id, subject, predicate, object_entity, object_value, evidence_type from api.claims
            where status <> 'deprecated' and predicate = %s and subject = any(%s)"""


def change_set(g: dict) -> dict:
    def claim(p, o):
        return {"op": "add_claim", "subject": g["iri"], "predicate": p, "object": o, "evidence_type": "inferred",
                "derived_from": g["premises"]}
    ops = [claim(RDF_TYPE, {"entity": OA + "Annotation"}), claim(DCT_TYPE, {"entity": g["kind"]}),
           claim(OA + "hasTarget", {"entity": g["target"]}), claim(OA + "bodyValue", {"value": {"@value": g["text"]}})]
    digest = hashlib.sha256(json.dumps([g["premises"], g["text"]]).encode()).hexdigest()[:16]
    return {"idempotency_key": f"map-gaps:{g['iri']}|{digest}", "attributed_to": AGENT, "target_scope": SCOPE,
            "ops": ops}


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(CLAIMS, (PREDICATES, LABEL, RDF_TYPE))
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(LABELS, (LABEL, sorted({r["subject"] for r in rows})))
        found = gaps(rows + [dict(zip(cols, r)) for r in cur.fetchall()])
        open_ = {g["iri"] for g in found}
        closed = [c for c in prior_claims(cur, AGENT, "map-gaps:", "") if c["subject"] not in open_]
        submitted = {}
        with run(cur, MODULE, {"gaps": len(found)}) as (run_id, outputs):
            for g in found:
                cs = change_set(g)
                old = prior_claims(cur, AGENT, f"map-gaps:{g['iri']}|", cs["idempotency_key"])
                if old:   # the premises changed: the new version replaces the old one
                    supersede(cs, old)
                submitted[g["iri"]] = str(submit(cur, cs, run_id)["id"])
            if closed:
                ids = sorted(str(c["id"]) for c in closed)
                submit(cur, {"idempotency_key": "map-gaps-closed:" + hashlib.sha256(" ".join(ids).encode()).hexdigest()[:16],
                             "attributed_to": AGENT, "target_scope": SCOPE,
                             "ops": [{"op": "set_status", "claim": str(c["id"]), "status": "deprecated",
                                      "reason": "retracted", "expect_status": c["status"]} for c in closed]}, run_id)
            outputs.update(gaps=len(found), closed=len({c["subject"] for c in closed}))
    return {"gaps": len(found), "closed": len({c["subject"] for c in closed}), "change_sets": submitted}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List what the organisation and process maps lack, as questions.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_map_gaps"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, patterns=manifest_patterns("map-gaps"))   # D-79
    return 0


if __name__ == "__main__":
    sys.exit(main())

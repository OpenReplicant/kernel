"""Conformance worker: compares each documented process with what was observed and submits every
discrepancy for review (D-50). Needs no runner.

It listens on kernel_change_set and runs a pass whenever a change set is applied, plus a sweep on start
and every KK_SWEEP_SECONDS. A pass reads the current process claims (api.claims), finds the
discrepancies (compare.py) and submits one change set per discrepancy in the review lane: an
oa:Annotation (dcterms:type its kind, oa:hasTarget the steps, oa:bodyValue a sentence), every claim
inferred and derived from the claims compared. The idempotency key carries a hash of the premises, so an
unchanged discrepancy is a no-op; a changed one supersedes the approved earlier version once approved;
a discrepancy that is no longer found has its claims retracted.

  python -m conformance [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_conformance), KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from kk.changes import prior as prior_claims, supersede
from kk.lib import connect, listen, run, submit

from .compare import CONTAINER, LABEL, MAX, NEXT, RESULT, discrepancies

MODULE = "urn:kk:module:conformance"
AGENT = "urn:kk:agent:conformance"
SCOPE = "urn:kk:scope:default"
OA = "http://www.w3.org/ns/oa#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
CLAIMS = """select id, subject, predicate, object_entity, object_value, qualifiers, evidence_type from api.claims
            where status <> 'deprecated' and (predicate = any(%(p)s)
               or (predicate = %(label)s and subject in (select subject from api.claims where predicate = %(c)s)))"""


def change_set(d: dict) -> dict:
    def claim(p, o):
        return {"op": "add_claim", "subject": d["iri"], "predicate": p, "object": o, "evidence_type": "inferred",
                "derived_from": d["premises"]}
    ops = [claim(RDF_TYPE, {"entity": OA + "Annotation"}), claim(DCT_TYPE, {"entity": d["kind"]}),
           *[claim(OA + "hasTarget", {"entity": t}) for t in d["targets"]],
           claim(OA + "bodyValue", {"value": {"@value": d["text"]}})]
    digest = hashlib.sha256(json.dumps([d["premises"], d["text"]]).encode()).hexdigest()[:16]
    return {"idempotency_key": f"conformance:{d['iri']}|{digest}", "attributed_to": AGENT, "target_scope": SCOPE,
            "lane": "review", "ops": ops}


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(CLAIMS, {"p": [NEXT, CONTAINER, RESULT, MAX], "label": LABEL, "c": CONTAINER})
        cols = [d.name for d in cur.description]
        found = discrepancies([dict(zip(cols, r)) for r in cur.fetchall()])
        stale = [c for c in prior_claims(cur, AGENT, "conformance:", "")
                 if c["subject"] not in {d["iri"] for d in found}]
        submitted = {}
        with run(cur, MODULE, {"discrepancies": len(found)}) as (run_id, outputs):
            for d in found:
                cs = change_set(d)
                prior = prior_claims(cur, AGENT, f"conformance:{d['iri']}|", cs["idempotency_key"])
                if prior:
                    supersede(cs, prior)
                submitted[d["iri"]] = str(submit(cur, cs, run_id)["id"])
            if stale:
                ids = sorted(str(c["id"]) for c in stale)
                cs = {"idempotency_key": "conformance-resolved:" + hashlib.sha256(" ".join(ids).encode()).hexdigest()[:16],
                      "attributed_to": AGENT, "target_scope": SCOPE,
                      "ops": [{"op": "set_status", "claim": str(c["id"]), "status": "deprecated", "reason": "retracted",
                               "expect_status": c["status"]} for c in stale]}
                submit(cur, cs, run_id)
            outputs.update(discrepancies=len(found), resolved=len(stale))
    return {"discrepancies": submitted, "resolved": len(stale)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare documented and observed processes; submit discrepancies.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_conformance"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, wake=lambda payload: json.loads(payload).get("status") == "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

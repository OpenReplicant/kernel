"""map-drift: compares the declared map with the calls observed, and the parts it declares with the parts the
repository has, and submits each gap for review (D-100, D-102).
Needs no runner; also `kk serve` part `drift` (opt in).

It listens on kernel_change_set and runs a pass when a dependency, an interface or an observed call is applied,
plus a sweep on start and every KK_SWEEP_SECONDS. A pass reads the current claims (compare.py) and submits one
change set per gap in the review lane, as the conformance processor does for processes: an oa:Annotation
(dcterms:type its kind, oa:hasTarget both nodes, oa:bodyValue a sentence), every claim inferred and derived
from the claims on both sides, the declared and the observed. A gap keeps its identity while it lasts, so
finding it again is a no-op; one no longer found has its claims retracted.

  python -m map_drift [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_map_drift), KK_SWEEP_SECONDS (default 30), KK_LOOKBACK_SECONDS (calls observed this recently count; default
3600).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from kk.changes import prior as prior_claims, supersede
from kk.lib import connect, listen, manifest_patterns, run, submit

from .compare import CONNECTS_TO, SKOS, components, declared, discrepancies, observed, predicates, settings

MODULE = "urn:kk:module:map-drift"
AGENT = "urn:kk:agent:map-drift"
SCOPE = "urn:kk:scope:default"
OA = "http://www.w3.org/ns/oa#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
BS = "https://w3id.org/knowledge-kernel/backstage#"
KKT = "https://w3id.org/knowledge-kernel/tosca#"
LOOKBACK = int(os.environ.get("KK_LOOKBACK_SECONDS", "3600"))

MAPPINGS = """select subject, predicate, object_entity from api.claims
              where status <> 'deprecated' and predicate = any(%s) and object_entity is not null"""
DECLARED = """select id, subject, predicate, object_entity from api.claims
              where status <> 'deprecated' and evidence_type <> 'observed' and object_entity is not null
                and predicate = any(%s)"""
OBSERVED = """select id, subject, object_entity, valid_to from api.claims
              where status <> 'deprecated' and evidence_type = 'observed' and predicate = %s
                and object_entity is not null and valid_to >= now() - make_interval(secs => %s)"""
SELF_ENCODER = "urn:kk:agent:self-encoder"
NODE_CLASSES = """select subject from api.claims where status <> 'deprecated' and object_entity = %s
                  and predicate = any(%s)"""
DECLARED_NODES = """select id, subject from api.claims where status <> 'deprecated' and attributed_to <> %s
                    and predicate = %s and object_entity = any(%s)"""
NODE_FIELDS = """select subject, predicate, object_value->>'@value' as value from api.claims
                 where status <> 'deprecated' and predicate = any(%s)"""
OBSERVED_NODES = """select id, subject, object_entity from api.claims where status <> 'deprecated'
                    and attributed_to = %s and predicate = %s and object_entity is not null"""
PROPERTIES = """select subject, coalesce(qualifiers->>%s, qualifiers->>%s) as key, object_value->>'@value' as value
                from api.claims where status <> 'deprecated' and predicate = any(%s)"""


def rows(cur, q, args) -> list[dict]:
    cur.execute(q, args)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def change_set(d: dict) -> dict:
    def claim(p, o):
        return {"op": "add_claim", "subject": d["iri"], "predicate": p, "object": o, "evidence_type": "inferred",
                "derived_from": d["premises"]}
    ops = [claim(RDF_TYPE, {"entity": OA + "Annotation"}), claim(DCT_TYPE, {"entity": d["kind"]}),
           *[claim(OA + "hasTarget", {"entity": t}) for t in d["targets"]],
           claim(OA + "bodyValue", {"value": {"@value": d["text"]}})]
    digest = hashlib.sha256(json.dumps([d["kind"], d["targets"], d["text"]]).encode()).hexdigest()[:16]
    return {"idempotency_key": f"map-drift:{d['iri']}|{digest}", "attributed_to": AGENT, "target_scope": SCOPE,
            "lane": "review", "ops": ops}


def find(cur) -> list[dict]:
    dep, req, cap = predicates(rows(cur, MAPPINGS, ([SKOS + m for m in ("exactMatch", "closeMatch", "relatedMatch")],)))
    want = declared(rows(cur, DECLARED, (sorted(dep | req | cap),)), dep, req, cap)
    alias, untraced = settings(rows(cur, PROPERTIES, (BS + "annotationKey", KKT + "propertyName",
                                                      [BS + "annotation", KKT + "propertyValue"])))
    seen = observed(rows(cur, OBSERVED, (CONNECTS_TO, LOOKBACK)), alias)
    # components (D-102): the classes mapped to kkt:NodeTemplate, the parts the map types with them, and the parts
    # the self-encoder found
    classes = [r["subject"] for r in rows(cur, NODE_CLASSES, (KKT + "NodeTemplate", [SKOS + "exactMatch", SKOS + "closeMatch"]))]
    nodes = rows(cur, DECLARED_NODES, (SELF_ENCODER, RDF_TYPE, classes)) if classes else []
    kinds: dict = {}
    for r in rows(cur, NODE_FIELDS, ([BS + "type", BS + "lifecycle"],)):
        t, lc = kinds.get(r["subject"], (None, None))
        kinds[r["subject"]] = (r["value"], lc) if r["predicate"] == BS + "type" else (t, r["value"])
    found = rows(cur, OBSERVED_NODES, (SELF_ENCODER, KKT + "nodeType"))
    return discrepancies(want, seen, untraced) + components(nodes, kinds, found)


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        found = find(cur)
        stale = [c for c in prior_claims(cur, AGENT, "map-drift:", "") if c["subject"] not in {d["iri"] for d in found}]
        submitted = {}
        with run(cur, MODULE, {"gaps": len(found)}) as (run_id, outputs):
            for d in found:
                cs = change_set(d)
                prior = prior_claims(cur, AGENT, f"map-drift:{d['iri']}|", cs["idempotency_key"])
                if prior:
                    supersede(cs, prior)
                submitted[d["iri"]] = {"kind": d["kind"].rsplit(":", 1)[-1], "text": d["text"],
                                       "change_set": str(submit(cur, cs, run_id)["id"])}
            if stale:
                ids = sorted(str(c["id"]) for c in stale)
                submit(cur, {"idempotency_key": "map-drift-resolved:" + hashlib.sha256(" ".join(ids).encode()).hexdigest()[:16],
                             "attributed_to": AGENT, "target_scope": SCOPE,
                             "ops": [{"op": "set_status", "claim": str(c["id"]), "status": "deprecated",
                                      "reason": "retracted", "expect_status": c["status"]} for c in stale]}, run_id)
            outputs.update(gaps=len(found), resolved=len(stale))
    return {"gaps": submitted, "resolved": len(stale)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare the declared map with observed calls; submit the gaps.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_map_drift"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, patterns=manifest_patterns("map-drift"))   # D-79
    return 0


if __name__ == "__main__":
    sys.exit(main())

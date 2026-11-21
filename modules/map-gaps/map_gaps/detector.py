"""The gap processor as a detector (M6-T0, D-110): `modules/detectors`' engine runs it from a cursor over what
the kernel applied, as role kkm_map_gaps (so it sees what that role is cleared for).

`find` reads the current map claims and lists the gaps (gaps.py); a gap is a match whose key is its IRI and a
digest of its premises and question, so a changed question is a gap that dissolved and one that formed.
`react` writes what the interviewer reads: for each formed gap one change set (an oa:Annotation, inferred,
derived from the claims that put its target on the map; the one from earlier premises is superseded), for
each dissolved gap that was not formed again its claims retracted. Both are idempotent: a pass that failed
is repeated.
"""
from __future__ import annotations

import hashlib
import json

from kk.changes import prior as prior_claims, supersede
from kk.lib import submit

from detectors.core import Match
from detectors.engine import Detector

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


def digest(g: dict) -> str:
    return hashlib.sha256(json.dumps([g["premises"], g["text"]]).encode()).hexdigest()[:16]


def change_set(g: dict) -> dict:
    def claim(p, o):
        return {"op": "add_claim", "subject": g["iri"], "predicate": p, "object": o, "evidence_type": "inferred",
                "derived_from": g["premises"]}
    ops = [claim(RDF_TYPE, {"entity": OA + "Annotation"}), claim(DCT_TYPE, {"entity": g["kind"]}),
           claim(OA + "hasTarget", {"entity": g["target"]}), claim(OA + "bodyValue", {"value": {"@value": g["text"]}})]
    return {"idempotency_key": f"map-gaps:{g['iri']}|{digest(g)}", "attributed_to": AGENT, "target_scope": SCOPE,
            "ops": ops}


def find(conn, anchors=None) -> list[Match]:
    with conn.cursor() as cur:
        cur.execute(CLAIMS, (PREDICATES, LABEL, RDF_TYPE))
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(LABELS, (LABEL, sorted({r["subject"] for r in rows})))
        found = gaps(rows + [dict(zip(cols, r)) for r in cur.fetchall()])
    return [Match(f"{g['iri']}|{digest(g)}", (g["target"],), tuple(g["premises"]), g) for g in found]


def react(conn, formed, dissolved, run_id) -> None:
    with conn.cursor() as cur:
        for m in formed:
            g = m.data
            cs = change_set(g)
            old = prior_claims(cur, AGENT, f"map-gaps:{g['iri']}|", cs["idempotency_key"])
            if old:   # the premises changed: the new version replaces the old one
                supersede(cs, old)
            submit(cur, cs, run_id)
        again = {m.key.split("|")[0] for m in formed}
        closed = [c for s in dissolved if (iri := s.key.split("|")[0]) not in again
                  for c in prior_claims(cur, AGENT, f"map-gaps:{iri}|", "")]
        if closed:
            ids = sorted(str(c["id"]) for c in closed)
            submit(cur, {"idempotency_key": "map-gaps-closed:" + hashlib.sha256(" ".join(ids).encode()).hexdigest()[:16],
                         "attributed_to": AGENT, "target_scope": SCOPE,
                         "ops": [{"op": "set_status", "claim": str(c["id"]), "status": "deprecated",
                                  "reason": "retracted", "expect_status": c["status"]} for c in closed]}, run_id)


DETECTOR = Detector("map-gaps", "kkm_map_gaps", find=find, react=react, scope=SCOPE, anchored=False,
                    prefixes=["map-gaps"])

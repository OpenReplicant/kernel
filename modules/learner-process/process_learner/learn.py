"""Confirmed discrepancies -> proposed revisions of the documented process (D-76). Pure; no database.

A discrepancy about an observed path (unexpected, skip, swap, rework) that a person confirmed, and that
practice takes in at least `share` of the process's cases, gives one proposal: the path a → b added to
the documented process (`bbo:has_nextFlowNode`, inferred), with a Web Annotation a reviewer reads
(`dcterms:type urn:kk:revision-kind:add-path`, `oa:hasTarget` the two steps, `oa:bodyValue` a
sentence), every claim derived from the confirmed discrepancy's claims, in the review lane. Its key is
the discrepancy's, so a rejected proposal is never made again.
"""
from __future__ import annotations

import hashlib

BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
OA = "http://www.w3.org/ns/oa#"
NEXT = BBO + "has_nextFlowNode"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
ADD_PATH = "urn:kk:revision-kind:add-path"
LEARNABLE = {"unexpected", "skip", "swap", "rework"}
AGENT = "urn:kk:agent:learner-process"
SCOPE = "urn:kk:scope:default"
SHARE = 0.05


def key(discrepancy: str) -> str:
    return f"learn:{discrepancy}"


def proposals(found: list[dict], confirmed: dict[str, list[str]], labels: dict[str, str],
              share: float = SHARE) -> list[dict]:
    """found: conformance's discrepancies (conformance.compare); confirmed: discrepancy IRI -> the ids of
    its current claims (a person approved it); labels: step IRI -> label. Change sets, sorted by key."""
    name = lambda s: labels.get(s) or s.rsplit(":", 1)[-1]  # noqa: E731
    out = []
    for d in found:
        kind = d["kind"].rsplit(":", 1)[-1]
        if kind not in LEARNABLE or d["iri"] not in confirmed or not d.get("edge") or not d.get("cases"):
            continue
        n, cases = d["count"] or 0, d["cases"]
        if n < share * cases:
            continue
        a, b = d["edge"]
        why = sorted(confirmed[d["iri"]])
        iri = "urn:kk:revision:" + hashlib.sha256(f"{d['process']}|add-path|{a}|{b}".encode()).hexdigest()[:16]

        def claim(s, p, o):
            return {"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": "inferred",
                    "derived_from": why}
        text = (f"Practice: {n:,} of {cases:,} cases ({n / cases:.0%}) went from {name(a)} to {name(b)}, confirmed "
                f"as a deviation from the documented process. Approve to make {name(a)} → {name(b)} part of it.")
        out.append({"idempotency_key": key(d["iri"]), "attributed_to": AGENT, "target_scope": SCOPE, "lane": "review",
                    "ops": [claim(a, NEXT, {"entity": b}),
                            claim(iri, RDF_TYPE, {"entity": OA + "Annotation"}),
                            claim(iri, DCT_TYPE, {"entity": ADD_PATH}),
                            *[claim(iri, OA + "hasTarget", {"entity": t}) for t in sorted({a, b})],
                            claim(iri, OA + "bodyValue", {"value": {"@value": text}})]})
    return sorted(out, key=lambda c: c["idempotency_key"])

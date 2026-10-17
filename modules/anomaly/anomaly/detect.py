"""Observations of services -> anomalies (D-53). Pure functions; no database.

Input: current observed claims about services (`urn:kk:node:*`, `sosa:hasSimpleResult`, one per
property and window) and documented limits (`<node> bbo:maxValue n` with qualifier
`sosa:observedProperty`). For each service and property, each of the latest windows is anomalous when

  its value exceeds the documented limit                                   limit
  it lies more than 3 standard deviations from the mean of the previous     zscore
  windows (at least 5 of them, up to 20)

An anomaly is an oa:Annotation on the service's node, valid for the observation's window, inferred and
derived from the observation (and the limit, or the windows it was compared with).
"""
from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict

SOSA = "http://www.w3.org/ns/sosa/"
OA = "http://www.w3.org/ns/oa#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
MAX = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#maxValue"
RESULT, PROP = SOSA + "hasSimpleResult", SOSA + "observedProperty"
LATEST, HISTORY, MIN_HISTORY, Z = 3, 20, 5, 3.0


def value(c: dict):
    try:
        return float((c.get("object_value") or {}).get("@value"))
    except (TypeError, ValueError):
        return None


def anomalies(observations: list[dict], limits: list[dict]) -> list[dict]:
    """[{iri, kind, target, property, value, text, premises, valid_from, valid_to}]"""
    limit = {}
    for c in limits:
        q = c.get("qualifiers") or {}
        if set(q) == {PROP} and value(c) is not None:
            limit[(c["subject"], q[PROP])] = (value(c), str(c["id"]))
    series = defaultdict(list)
    for c in observations:
        q = c.get("qualifiers") or {}
        if set(q) == {PROP} and value(c) is not None and c.get("valid_from"):
            series[(c["subject"], q[PROP])].append(c)
    out = []
    for (node, prop), obs in sorted(series.items()):
        obs.sort(key=lambda c: c["valid_from"])
        name, pname = node.rsplit(":", 1)[-1], prop.rsplit(":", 1)[-1]
        for i in range(max(0, len(obs) - LATEST), len(obs)):
            c, v = obs[i], value(obs[i])

            def add(kind, text, premises):
                key = hashlib.sha256(f"{c['id']}|{kind}".encode()).hexdigest()[:16]
                out.append({"iri": f"urn:kk:anomaly:{key}", "kind": f"urn:kk:anomaly-kind:{kind}", "target": node,
                            "property": prop, "value": v, "text": text, "premises": premises,
                            "valid_from": c["valid_from"], "valid_to": c.get("valid_to")})

            if (node, prop) in limit and v > limit[(node, prop)][0]:
                lim, lid = limit[(node, prop)]
                add("limit", f"{name}: {pname} {v:g} exceeds the documented limit {lim:g}.", [str(c["id"]), lid])
            history = obs[max(0, i - HISTORY):i]
            values = [value(h) for h in history]
            if len(values) >= MIN_HISTORY:
                mean, sd = statistics.fmean(values), statistics.pstdev(values)
                if sd > 0 and abs(v - mean) / sd > Z:
                    add("zscore", f"{name}: {pname} {v:g} is {abs(v - mean) / sd:.1f} standard deviations from "
                        f"the mean {mean:g} of the previous {len(values)} windows.",
                        [str(c["id"])] + [str(h["id"]) for h in history])
    return out


def change_set(a: dict, agent: str, scope: str) -> dict:
    valid = {k: str(a[k]) for k in ("valid_from", "valid_to") if a.get(k)}

    def claim(p, o):
        return {"op": "add_claim", "subject": a["iri"], "predicate": p, "object": o, "evidence_type": "inferred",
                "derived_from": a["premises"], **valid}
    return {"idempotency_key": f"anomaly:{a['iri']}", "attributed_to": agent, "target_scope": scope,
            "ops": [claim(RDF_TYPE, {"entity": OA + "Annotation"}), claim(DCT_TYPE, {"entity": a["kind"]}),
                    claim(OA + "hasTarget", {"entity": a["target"]}),
                    claim(OA + "bodyValue", {"value": {"@value": a["text"]}})]}

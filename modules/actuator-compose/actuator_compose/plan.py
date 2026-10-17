"""Approved plan steps -> the actions the actuator may carry out (D-53). Pure functions; no database.

A plan step is a `sosa:Actuation` that is `pplan:isStepOfPlan` a plan, with `dcterms:type` an action
(urn:kk:action:scale | restart), `sosa:hasFeatureOfInterest` the service's node (urn:kk:node:<service>)
and, for scale, the replica count as `sosa:hasSimpleResult`. A step is carried out only if

  the change set that recorded it went through the review lane and a person other than its submitter
  approved it; the action is on the fixed list; the service is on the allow list; a scale stays within
  1..max replicas; and this actuator has not run the step before.
"""
from __future__ import annotations

PPLAN_STEP_OF = "http://purl.org/net/p-plan#isStepOfPlan"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
SOSA = "http://www.w3.org/ns/sosa/"
ACTIONS = {"urn:kk:action:scale": "scale", "urn:kk:action:restart": "restart"}


def steps(claims: list[dict], change_sets: dict[str, dict]) -> list[dict]:
    """claims: current claims about plan steps (id, subject, predicate, object_entity, object_value,
    change_set); change_sets: id -> {lane, decided_by, submitted_by, status}."""
    by = {}
    for c in claims:
        by.setdefault(c["subject"], []).append(c)
    out = []
    for step, cs in sorted(by.items()):
        get = lambda p: next((c for c in cs if c["predicate"] == p), None)
        part = get(PPLAN_STEP_OF)
        if not part or not any(c["predicate"] == RDF_TYPE and c["object_entity"] == SOSA + "Actuation" for c in cs):
            continue
        action, target, result = get(DCT_TYPE), get(SOSA + "hasFeatureOfInterest"), get(SOSA + "hasSimpleResult")
        approvals = [change_sets.get(str(c["change_set"]), {}) for c in cs]
        out.append({"step": step, "plan": part["object_entity"], "claims": sorted(str(c["id"]) for c in cs),
                    "action": ACTIONS.get(action["object_entity"]) if action else None,
                    "service": (target["object_entity"] or "").removeprefix("urn:kk:node:") if target else None,
                    "replicas": (result["object_value"] or {}).get("@value") if result else None,
                    "approved": bool(approvals) and all(a.get("lane") == "review" and a.get("decided_by")
                                                        and a["decided_by"] != a.get("submitted_by")
                                                        and a.get("status") == "applied" for a in approvals)})
    return out


def refusal(s: dict, allowed: set[str], max_replicas: int) -> str | None:
    """Why the actuator will not carry out step s, or None."""
    if not s["approved"]:
        return "not approved by a person in the review lane"
    if s["action"] is None:
        return "the action is not on the fixed list (scale, restart)"
    if s["service"] not in allowed:
        return f"service {s['service']!r} is not on the allow list"
    if s["action"] == "scale":
        n = s["replicas"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= max_replicas:
            return f"scale needs a whole number of replicas from 1 to {max_replicas}"
    return None

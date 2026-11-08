"""The declared map against the calls observed (D-100). Pure functions over claim rows.

Declared: the current claims that state a dependency between two nodes, in any vocabulary the mappings pack
relates to kk-tosca's (`backstage:dependsOn skos:closeMatch kkt:dependsOn`); and, for a vocabulary that names
the interface between them (`backstage:consumesApi` and `providesApi`, mapped to kkt:hasRequirement and
kkt:hasCapability), a consumer and a provider of the same interface. Observed: kkt:connectsTo claims with
evidence `observed` (the metrics observer's service graph) in the lookback.

A discrepancy is claimed only where its evidence could exist:

  undeclared-call  a call was observed that the map does not declare
  unobserved-call  the map declares a call from a node that is sending calls to one that is seen, and no such
                   call was observed (an interface the source marks as untraced does not count)

Names: an observed node may be named by the service name its telemetry carries; a node's annotation or
property whose key ends in `otel-service` (or is `service.name`) gives that name. One whose key ends in
`untraced` lists the interfaces (by name) the node uses without traced calls.
"""
from __future__ import annotations

import hashlib

KKT = "https://w3id.org/knowledge-kernel/tosca#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
DEPENDS = {KKT + "dependsOn", KKT + "connectsTo", KKT + "hostedOn"}
REQUIRES, PROVIDES = KKT + "hasRequirement", KKT + "hasCapability"
CONNECTS_TO = KKT + "connectsTo"
KIND = "urn:kk:discrepancy-kind:"


def predicates(mappings: list[dict]) -> tuple[set, set, set]:
    """(dependency, consumer, provider) predicates: kk-tosca's own and every predicate mapped to them.
    mappings: rows (subject, predicate, object_entity) of skos:*Match claims."""
    dep, req, cap = set(DEPENDS), {REQUIRES}, {PROVIDES}
    for m in mappings:
        how, target = m["predicate"], m["object_entity"]
        if target in DEPENDS and how in (SKOS + "exactMatch", SKOS + "closeMatch"):
            dep.add(m["subject"])
        elif target == REQUIRES and how in (SKOS + "exactMatch", SKOS + "closeMatch", SKOS + "relatedMatch"):
            req.add(m["subject"])
        elif target == PROVIDES and how in (SKOS + "exactMatch", SKOS + "closeMatch"):
            cap.add(m["subject"])
    return dep, req, cap


def name(iri: str) -> str:
    return iri.rsplit(":", 1)[-1].rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def declared(claims: list[dict], dep: set, req: set, cap: set) -> dict:
    """(source, target) -> {"via": [interface names], "premises": [claim ids]}, from current non-observed claims."""
    edges: dict = {}
    providers: dict = {}
    for c in claims:
        if c["predicate"] in cap and c["object_entity"]:
            providers.setdefault(c["object_entity"], []).append(c)

    def add(a, b, via, ids):
        e = edges.setdefault((a, b), {"via": [], "premises": []})
        if via and via not in e["via"]:
            e["via"].append(via)
        e["premises"] += [i for i in ids if i not in e["premises"]]
    for c in claims:
        if not c["object_entity"] or c["subject"] == c["object_entity"]:
            continue
        if c["predicate"] in dep:
            add(c["subject"], c["object_entity"], None, [str(c["id"])])
        elif c["predicate"] in req:
            for p in providers.get(c["object_entity"], []):
                if p["subject"] != c["subject"]:
                    add(c["subject"], p["subject"], name(c["object_entity"]), [str(c["id"]), str(p["id"])])
    return edges


def observed(claims: list[dict], alias: dict) -> dict:
    """(source, target) -> the latest observed claim id, with observed names resolved to the map's nodes."""
    edges: dict = {}
    for c in sorted(claims, key=lambda c: str(c["valid_to"])):
        a, b = alias.get(c["subject"], c["subject"]), alias.get(c["object_entity"], c["object_entity"])
        if a != b:
            edges[(a, b)] = str(c["id"])
    return edges


def settings(properties: list[dict]) -> tuple[dict, dict]:
    """(alias: observed node -> map node, untraced: node -> interface names) from rows (subject, key, value)."""
    alias, untraced = {}, {}
    for p in properties:
        key, value = str(p["key"] or ""), str(p["value"] or "")
        if key.endswith("otel-service") or key == "service.name":
            alias[f"urn:kk:node:{value}"] = p["subject"]
        elif key.endswith("untraced"):
            untraced.setdefault(p["subject"], set()).update(x.strip() for x in value.split(",") if x.strip())
    return alias, untraced


def label(iri: str) -> str:
    return iri.removeprefix("urn:kk:node:")


def discrepancies(want: dict, seen: dict, untraced: dict) -> list[dict]:
    """Each: iri, kind, targets, text, premises (claim ids on both sides)."""
    senders = {a for a, _ in seen}
    visible = senders | {b for _, b in seen}
    out = []
    for (a, b), oid in sorted(seen.items()):
        if (a, b) not in want:
            # the other side: what the map does declare about the caller's calls, if anything
            stated = [i for (x, _), e in sorted(want.items()) if x == a for i in e["premises"]]
            out.append({"kind": KIND + "undeclared-call", "source": a, "target": b,
                        "premises": [oid] + list(dict.fromkeys(stated)),
                        "text": f"{label(a)} calls {label(b)}, which the map does not declare."})
    for (a, b), e in sorted(want.items()):
        if (a, b) in seen or a not in senders or b not in visible:
            continue
        if e["via"] and set(e["via"]) <= untraced.get(a, set()):
            continue
        evidence = [next(i for (x, _), i in sorted(seen.items()) if x == a)]
        evidence += [i for (x, y), i in sorted(seen.items()) if b in (x, y)][:1]
        via = f" (through {', '.join(e['via'])})" if e["via"] else ""
        out.append({"kind": KIND + "unobserved-call", "source": a, "target": b,
                    "premises": e["premises"] + [i for i in dict.fromkeys(evidence) if i not in e["premises"]],
                    "text": f"The map says {label(a)} calls {label(b)}{via}; both are active and no such call was observed."})
    for d in out:
        tag = hashlib.sha256(f"{d['kind']}|{d['source']}|{d['target']}".encode()).hexdigest()[:16]
        d.update(iri=f"urn:kk:drift:{tag}", targets=[d["source"], d["target"]])
    return out

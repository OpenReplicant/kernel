"""Projection of claims into a graph (from the parked graph decoder, T9). Pure functions, stdlib only.

Nodes are entities; edges are entity-valued claims (rdf:type becomes the node's kind instead). Every edge
carries its claim id, belief and status, so the picture traces back to claims. Filters select a
neighbourhood: a module (its slots, requirements, affordances and node templates), a slot (the modules
that fill or require it), or a root entity with a depth.
"""
from __future__ import annotations

import re
from collections import defaultdict

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
KK = "https://w3id.org/knowledge-kernel/ns#"
KKT = "https://w3id.org/knowledge-kernel/tosca#"
KINDS = {KK + "Module": "module", KK + "Slot": "slot", KKT + "NodeTemplate": "node",
         KKT + "ServiceTemplate": "service", "http://www.w3.org/2002/07/owl#Ontology": "ontology"}
HIDDEN_PREDICATES = {RDF_TYPE, "http://purl.org/dc/terms/license"}


def local(iri: str) -> str:
    return re.split(r"[#/:]", iri.rstrip("/#"))[-1] or iri


def build(claims: list[dict]) -> dict:
    """claims: rows of api.claims (id, subject, predicate, object_entity, object_value, status, belief)."""
    nodes: dict[str, dict] = {}
    edges = []

    def node(iri):
        return nodes.setdefault(iri, {"id": iri, "label": local(iri), "kind": "entity"})

    live = [c for c in claims if c["status"] != "deprecated"]
    for c in live:
        if c["predicate"] == RDF_TYPE and c["object_entity"] in KINDS:
            node(c["subject"])["kind"] = KINDS[c["object_entity"]]
        elif c["predicate"] == LABEL and c.get("object_value"):
            node(c["subject"])["label"] = str(c["object_value"].get("@value"))
    for c in live:
        if not c["object_entity"] or c["predicate"] in HIDDEN_PREDICATES:
            continue
        node(c["subject"]), node(c["object_entity"])
        edges.append({"id": c["id"], "source": c["subject"], "target": c["object_entity"],
                      "predicate": c["predicate"], "label": local(c["predicate"]),
                      "belief": c.get("belief"), "status": c["status"]})
    return {"nodes": nodes, "edges": edges}


def neighbourhood(g: dict, root: str, depth: int) -> set[str]:
    adj = defaultdict(set)
    for e in g["edges"]:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    seen, frontier = {root}, {root}
    for _ in range(depth):
        frontier = {n for f in frontier for n in adj[f]} - seen
        seen |= frontier
    return seen


def select(g: dict, module: str | None = None, slot: str | None = None, root: str | None = None,
           depth: int = 1) -> set[str] | None:
    """Node ids to keep, or None for the whole graph."""
    out = lambda s, p: {e["target"] for e in g["edges"] if e["source"] == s and e["predicate"] == p}  # noqa: E731
    into = lambda o, p: {e["source"] for e in g["edges"] if e["target"] == o and e["predicate"] == p}  # noqa: E731
    keep: set[str] | None = None

    def add(ids):
        nonlocal keep
        keep = (keep or set()) | ids

    if module:
        add({module} | out(module, KK + "fills") | out(module, KKT + "hasRequirement")
            | out(module, KKT + "hasAffordance") | out(module, KK + "runsAs") | into(module, KKT + "nodeType"))
    if slot:
        add({slot} | into(slot, KK + "fills") | into(slot, KKT + "hasRequirement"))
    if root:
        add(neighbourhood(g, root, depth))
    return keep


def cytoscape(g: dict, keep: set[str] | None) -> dict:
    nodes = [n for n in g["nodes"].values() if keep is None or n["id"] in keep]
    ids = {n["id"] for n in nodes}
    edges = [e for e in g["edges"] if e["source"] in ids and e["target"] in ids]
    return {"nodes": [{"data": n} for n in sorted(nodes, key=lambda n: n["id"])],
            "edges": [{"data": e} for e in sorted(edges, key=lambda e: (e["source"], e["label"], e["id"]))]}

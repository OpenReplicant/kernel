"""Organisation and process maps -> what they lack (M5-T4). Pure functions; no database.

Input: current claims (dicts with id, subject, predicate, object_entity, object_value, evidence_type).
The maps are the ones people describe: a process step counts when its `bbo:has_container` claim was
extracted (an interview, a written procedure), so event logs and BPMN files raise no questions; every
`org:Post` counts. A gap is a question to ask next:

  no-performer   a step nobody does (no bbo:has_resource)          "Who does Check budget?"
  no-next        a step with nothing after it (bbo:has_nextFlowNode; an end event closes it)
  no-previous    a step nothing leads to (a start event, or another step, closes it)
  unheld         a post nobody holds (org:heldBy, or a person's org:holds)
  no-manager     a post that reports to no one while another post does not either (one top post is the head)
  no-unit        a post in no unit or organisation (org:postIn, or the unit's org:hasPost)

Each gap is an oa:Annotation (dcterms:type urn:kk:gap-kind:<kind>, oa:hasTarget the step or post,
oa:bodyValue the question), derived from the claims that make its target part of the map.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
ORG = "http://www.w3.org/ns/org#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
CONTAINER, NEXT, RESOURCE = BBO + "has_container", BBO + "has_nextFlowNode", BBO + "has_resource"
HOLDS, HELD_BY, POST_IN, HAS_POST, REPORTS_TO = (ORG + t for t in ("holds", "heldBy", "postIn", "hasPost", "reportsTo"))
PREDICATES = [RDF_TYPE, LABEL, CONTAINER, NEXT, RESOURCE, HOLDS, HELD_BY, POST_IN, HAS_POST, REPORTS_TO]
KIND = "urn:kk:gap-kind:"
ORDER = ["no-performer", "no-next", "no-previous", "unheld", "no-manager", "no-unit"]


def gaps(claims: list[dict]) -> list[dict]:
    """[{iri, kind, target, text, premises}], in the order a person would be asked."""
    label: dict[str, str] = {}
    by = defaultdict(lambda: defaultdict(set))       # predicate -> subject -> objects
    steps: dict[str, list[str]] = defaultdict(list)  # step -> its extracted has_container claim ids
    posts: dict[str, list[str]] = defaultdict(list)  # post -> its typing claim ids
    for c in claims:
        s, p, o = c["subject"], c["predicate"], c.get("object_entity")
        if p == LABEL:
            v = (c.get("object_value") or {}).get("@value")
            if v is not None:
                label.setdefault(s, str(v))
            continue
        if o is None:
            continue
        by[p][s].add(o)
        if p == CONTAINER and c.get("evidence_type") == "extracted" and ":step:" in s:
            steps[s].append(str(c["id"]))
        if p == RDF_TYPE and o == ORG + "Post":
            posts[s].append(str(c["id"]))
    name = lambda x: label.get(x) or x.rsplit(":", 1)[-1]  # noqa: E731
    incoming = {o for objs in by[NEXT].values() for o in objs}
    held = set(by[HELD_BY]) | {o for objs in by[HOLDS].values() for o in objs}
    placed = set(by[POST_IN]) | {o for objs in by[HAS_POST].values() for o in objs}
    out = []

    def add(kind, target, text, premises):
        iri = "urn:kk:gap:" + hashlib.sha256(f"{kind}|{target}".encode()).hexdigest()[:16]
        out.append({"iri": iri, "kind": KIND + kind, "target": target, "text": text, "premises": sorted(premises)})

    for s in sorted(steps):
        if not by[RESOURCE].get(s):
            add("no-performer", s, f"Who does “{name(s)}”?", steps[s])
        if not by[NEXT].get(s):
            add("no-next", s, f"What happens after “{name(s)}”, or does the process end there?", steps[s])
        if s not in incoming:
            add("no-previous", s, f"What comes before “{name(s)}”, or does the process start there?", steps[s])
    tops = [p for p in posts if not by[REPORTS_TO].get(p)]
    for p in sorted(posts):
        if p not in held:
            add("unheld", p, f"Who holds the post of {name(p)}?", posts[p])
        if p in tops and len(tops) > 1:
            add("no-manager", p, f"Whom does the {name(p)} report to?", posts[p])
        if p not in placed:
            add("no-unit", p, f"Which team or organisation is the {name(p)} part of?", posts[p])
    rank = {KIND + k: i for i, k in enumerate(ORDER)}
    return sorted(out, key=lambda g: (rank[g["kind"]], g["target"]))

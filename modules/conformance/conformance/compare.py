"""Documented vs observed process -> discrepancies (D-50). Pure functions; no database.

Input: current claims (dicts with id, subject, predicate, object_entity, object_value, qualifiers,
evidence_type). The documented process is the `bbo:has_nextFlowNode` claims with evidence deterministic
or extracted (BPMN encoder, process-extraction skill); a task's documented successors are the tasks
reachable through gateways and events. The observed process is the `observed` edges and the SOSA
observations of the event-log encoder. For each process:

  observed a→b, b not a documented successor of a:
    a = b                                  rework
    b documented two tasks after a (via x)  skip (x)
    a documented successor of b            swap
    otherwise                              unexpected
  documented a→b never observed            missing      (only for processes with observations)
  wait-seconds-max of b after a > the documented bbo:maxValue limit         limit

Each discrepancy lists the claims it was derived from: the observed edge and its counts (or the
waits), and the documented claims it contradicts.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
SOSA = "http://www.w3.org/ns/sosa/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
NEXT, CONTAINER, MAX = BBO + "has_nextFlowNode", BBO + "has_container", BBO + "maxValue"
RESULT, OBSERVED_PROPERTY, PREV = SOSA + "hasSimpleResult", SOSA + "observedProperty", BBO + "has_previousFlowNode"
PROPERTY = "urn:kk:property:"
DOCUMENTED = {"deterministic", "extracted"}
KIND = "urn:kk:discrepancy-kind:"


def is_step(iri: str) -> bool:
    return ":step:" in iri


def value(c: dict):
    return (c.get("object_value") or {}).get("@value")


def discrepancies(claims: list[dict]) -> list[dict]:
    """[{iri, process, kind, targets, text, premises}] sorted by iri."""
    container, labels = {}, {}
    for c in claims:
        if c["predicate"] == CONTAINER and c["object_entity"]:
            container[c["subject"]] = c["object_entity"]
        if c["predicate"] == LABEL and value(c) is not None:
            labels.setdefault(c["subject"], str(value(c)))
    name = lambda s: labels.get(s) or s.rsplit(":", 1)[-1]

    doc = defaultdict(lambda: defaultdict(list))      # process -> node -> [(next, claim id)]
    seen = defaultdict(lambda: defaultdict(list))     # process -> (a, b) -> [claim ids]
    counts, longest, limits = defaultdict(list), defaultdict(list), {}
    for c in claims:
        p, s, o = container.get(c["subject"]), c["subject"], c["object_entity"]
        q = c.get("qualifiers") or {}
        if c["predicate"] == NEXT and p and o:
            if c["evidence_type"] in DOCUMENTED:
                doc[p][s].append((o, str(c["id"])))
            elif c["evidence_type"] == "observed" and is_step(s) and is_step(o):
                seen[p][(s, o)].append(str(c["id"]))
        elif c["predicate"] == RESULT and q.get(PREV):
            prop = q.get(OBSERVED_PROPERTY, "")
            if prop == PROPERTY + "directly-follows-count":
                counts[(q[PREV], s)].append((value(c), str(c["id"])))
            elif prop == PROPERTY + "wait-seconds-max":
                longest[(q[PREV], s)].append((value(c), str(c["id"])))
        elif c["predicate"] == MAX and q.get(PREV) and q.get(OBSERVED_PROPERTY) == PROPERTY + "wait-seconds":
            limits[(q[PREV], s)] = (value(c), str(c["id"]))

    out = []
    for p in sorted(set(doc) | set(seen)):
        graph = doc[p]

        def successors(a: str) -> dict[str, list[str]]:
            """Tasks reachable from a through non-task nodes -> the documented claims on the way."""
            found, todo, done = {}, [(a, [])], set()
            while todo:
                node, path = todo.pop()
                for nxt, cid in graph.get(node, []):
                    if is_step(nxt):
                        found.setdefault(nxt, path + [cid])
                    elif nxt not in done:
                        done.add(nxt)
                        todo.append((nxt, path + [cid]))
            return found

        def add(kind, a, b, text, premises, extra=()):
            key = hashlib.sha256(f"{p}|{kind}|{a}|{b}".encode()).hexdigest()[:16]
            out.append({"iri": f"urn:kk:discrepancy:{key}", "process": p, "kind": KIND + kind,
                        "targets": sorted({a, b, *extra}), "text": text, "premises": sorted(set(premises))})

        if not doc[p]:
            continue                                   # nothing documented to compare with
        for (a, b), edge_ids in sorted(seen[p].items()):
            succ = successors(a)
            if b in succ:
                continue
            n = sum(v or 0 for v, _ in counts[(a, b)])
            ev = edge_ids + [cid for _, cid in counts[(a, b)]]
            doc_ids = [cid for path in succ.values() for cid in path]
            between = [x for x, path in succ.items() if b in successors(x)]
            if a == b:
                add("rework", a, b, f"{name(a)} directly repeated itself in {n} case(s); the documented process "
                    f"goes on to {', '.join(name(x) for x in sorted(succ)) or 'nothing'}.", ev + doc_ids)
            elif between:
                x = between[0]
                add("skip", a, b, f"{n} case(s) went from {name(a)} to {name(b)} without {name(x)}, which the "
                    f"documented process puts between them.", ev + doc_ids + successors(x)[b], [x])
            elif a in successors(b):
                add("swap", a, b, f"{n} case(s) did {name(b)} after {name(a)}; the documented order is "
                    f"{name(b)} then {name(a)}.", ev + doc_ids + successors(b)[a])
            else:
                add("unexpected", a, b, f"{n} case(s) went from {name(a)} to {name(b)}, a path the documented "
                    f"process does not have.", ev + doc_ids)
        if seen[p]:
            for a in sorted(n for n in graph if is_step(n)):
                for b, path in sorted(successors(a).items()):
                    if (a, b) not in seen[p]:
                        add("missing", a, b, f"The documented path {name(a)} → {name(b)} was never observed.", path)
        for (a, b), (limit, lid) in sorted(limits.items()):
            if container.get(b) != p:
                continue
            over = [(v, cid) for v, cid in longest[(a, b)] if v is not None and limit is not None and v > limit]
            if over:
                worst = max(v for v, _ in over)
                add("limit", a, b, f"The wait from {name(a)} to {name(b)} reached {worst / 3600:.0f} h; the "
                    f"documented limit is {limit / 3600:.0f} h.", [lid] + [cid for _, cid in over])
    return sorted(out, key=lambda d: d["iri"])

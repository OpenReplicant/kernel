"""Wikidata's property constraints over claims (D-108): which claims break a constraint. Pure functions over plain
rows, standard library only; no database, no model.

A constraint is a claim on a property's term (subject wdt:P527, predicate wdt:P2302 "property constraint") whose
object is the kind of constraint, with its parameters as qualifiers. The four kinds Substrate's store checks
(`store.violation`, S-139), by Wikidata's own items:

  single-value  wd:Q19474404  a subject has at most one usable value for the property
  format        wd:Q21502404  a string value matches every pattern in wdt:P1793, as a whole
  one-of        wd:Q21510859  an entity value is one of the items in wdt:P2305
  value-type    wd:Q21510865  an entity value is an instance (wdt:P31, then wdt:P279 upwards) of a class in wdt:P2308

Rows in: dicts with id, subject, predicate, object_entity, object_value, qualifiers (as `substrate.usable` gives
them: the claims in force by Wikidata's rank rule). Reported, never blocking: this says what is wrong; it changes
no claim. Any other constraint kind (Wikidata has some forty) is not checked here and is not a violation.
"""
from __future__ import annotations

import json
import re

WDT = "http://www.wikidata.org/prop/direct/"
WD = "http://www.wikidata.org/entity/"
XSD_ANYURI = "http://www.w3.org/2001/XMLSchema#anyURI"
METHOD = "urn:kk:module:constraints-wikidata"
CONSTRAINT = WDT + "P2302"
INSTANCE_OF, SUBCLASS_OF = WDT + "P31", WDT + "P279"
PATTERN, CLASS, ALLOWED = WDT + "P1793", WDT + "P2308", WDT + "P2305"
KINDS = {WD + "Q19474404": "single-value", WD + "Q21502404": "format", WD + "Q21510859": "one-of",
         WD + "Q21510865": "value-type"}


def short(iri: str) -> str:
    """The Qid of urn:kk:substrate:Q13, else what follows the last # / or :."""
    return iri[len("urn:kk:substrate:"):] if iri.startswith("urn:kk:substrate:") else re.split(r"[#/:]", iri)[-1]


def qvals(qualifiers: dict | None, prop: str) -> list:
    """The values of one qualifier: a single value, or a list when it repeats."""
    v = (qualifiers or {}).get(prop)
    return [] if v is None else v if isinstance(v, list) else [v]


def string_of(row: dict) -> str | None:
    """A claim's value when it is a plain string or a URL (Substrate's string, external-id and url datatypes);
    a time or a quantity is typed and not a string here."""
    v = row.get("object_value")
    if row.get("object_entity") is None and isinstance(v, dict) and isinstance(v.get("@value"), str) \
            and v.get("@type") in (None, XSD_ANYURI):
        return v["@value"]
    return None


def classes_of(entity: str, edges: dict[str, dict[str, set[str]]]) -> set[str]:
    """What the entity is an instance of: its classes, and theirs upwards through subclass of."""
    seen: set[str] = set()
    todo = list(edges["instance"].get(entity, ()))
    while todo:
        c = todo.pop()
        if c not in seen:
            seen.add(c)
            todo.extend(edges["subclass"].get(c, ()))
    return seen


def violations(constraints: list[dict], claims: list[dict], hierarchy: list[dict] | None = None,
               problems: list[dict] | None = None) -> dict[str, list[dict]]:
    """claim id -> its violations, each {constraint (a claim id), kind, property, message}, in a fixed order.
    constraints: the usable claims whose predicate is P2302; claims: the usable claims of the constrained
    properties; hierarchy: the usable P31 and P279 claims (entity values only). A pattern that is not a regular
    expression checks nothing and is added to `problems` when given."""
    edges: dict[str, dict[str, set[str]]] = {"instance": {}, "subclass": {}}
    for h in hierarchy or []:
        if h.get("object_entity"):
            edges["instance" if h["predicate"] == INSTANCE_OF else "subclass"].setdefault(h["subject"], set()).add(h["object_entity"])
    by_property: dict[str, list[tuple[dict, str]]] = {}
    for c in constraints:
        kind = KINDS.get(c.get("object_entity"))
        if kind:
            by_property.setdefault(c["subject"], []).append((c, kind))
    found: dict[str, list[dict]] = {}

    def report(claim: dict, c: dict, kind: str, message: str) -> None:
        found.setdefault(str(claim["id"]), []).append(
            {"constraint": str(c["id"]), "kind": kind, "property": claim["predicate"], "message": message})

    groups: dict[tuple, list[dict]] = {}
    for claim in claims:
        for c, kind in by_property.get(claim["predicate"], ()):
            if kind == "single-value":
                groups.setdefault((claim["subject"], claim["predicate"], str(c["id"])), []).append(claim)
            elif kind == "format":
                text = string_of(claim)
                for pattern in qvals(c.get("qualifiers"), PATTERN):
                    try:
                        ok = text is None or re.fullmatch(str(pattern), text) is not None
                    except re.error as e:
                        if problems is not None:
                            problems.append({"constraint": str(c["id"]), "pattern": pattern, "error": str(e)})
                        continue
                    if not ok:
                        report(claim, c, kind, f"{json.dumps(text, ensure_ascii=False)} does not match the format {pattern}")
            elif kind == "one-of" and claim.get("object_entity") is not None:
                if claim["object_entity"] not in qvals(c.get("qualifiers"), ALLOWED):
                    report(claim, c, kind, f"{short(claim['object_entity'])} is not one of the values "
                                           f"{short(claim['predicate'])} allows")
            elif kind == "value-type" and claim.get("object_entity") is not None:
                if not set(qvals(c.get("qualifiers"), CLASS)) & classes_of(claim["object_entity"], edges):
                    report(claim, c, kind, f"{short(claim['object_entity'])} is not an instance of a class "
                                           f"{short(claim['predicate'])} allows")
    for (subject, prop, constraint), members in groups.items():
        if len(members) > 1:
            c = next(c for c, _ in by_property[prop] if str(c["id"]) == constraint)
            for claim in members:
                report(claim, c, "single-value", f"{short(subject)} has {len(members)} usable values for "
                                                 f"{short(prop)}; it takes one")
    return {k: sorted(v, key=lambda x: (x["kind"], x["constraint"], x["message"])) for k, v in sorted(found.items())}


def verdicts(found: dict[str, list[dict]], latest: dict[str, tuple[str, list[dict] | None]]) -> list[tuple[str, str, dict]]:
    """(claim id, verdict, details) for every claim whose recorded verdict should change.
    found: claim id -> its violations now; latest: claim id -> (this method's latest verdict, its violations).
    inconclusive says a constraint is broken (a report, not a refutation: the claim may be right and the
    constraint wrong); supported says it is not, any more, after an inconclusive."""
    out = []
    for claim in sorted(set(found) | set(latest)):
        now, (verdict, before) = found.get(claim, []), latest.get(claim, (None, None))
        if now and not (verdict == "inconclusive" and before == now):
            out.append((claim, "inconclusive", {"check": "property constraints", "violations": now}))
        elif not now and verdict == "inconclusive":
            out.append((claim, "supported", {"check": "property constraints", "violations": []}))
    return out

"""Matches, their diff and the claims that record events. Pure functions: no database, no psycopg.

A detector finds matches: a key (what the match is), the nodes it names (every entity whose change could
change it, also those under a negated pattern) and the claim ids it was made from. The engine compares the
matches it finds now with the matches it stored before, and records the difference as events:

  formed      a match that holds now and was not stored
  dissolved   a stored match that no longer holds

What is stored is claims (D-110): an event is a prov:Activity (dcterms:type urn:kk:event-kind:formed or
:dissolved, as:instrument the detector, prov:used the match, dcterms:relation each node, as:published the
time), the match is a prov:Entity (dcterms:type the detector, rdfs:label its key). A match holds while its
latest event, in the order the kernel applied them, is `formed`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
DCT_TYPE = "http://purl.org/dc/terms/type"
RELATION = "http://purl.org/dc/terms/relation"
PROV = "http://www.w3.org/ns/prov#"
AS = "https://www.w3.org/ns/activitystreams#"
USED, ACTIVITY, ENTITY = PROV + "used", PROV + "Activity", PROV + "Entity"
INSTRUMENT, PUBLISHED = AS + "instrument", AS + "published"
XSD_DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
FORMED, DISSOLVED = "urn:kk:event-kind:formed", "urn:kk:event-kind:dissolved"


@dataclass(frozen=True)
class Match:
    key: str
    nodes: tuple = ()
    claims: tuple = ()            # ids of the claims it was made from (premises of the events)
    data: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class Stored:
    """A match that holds, as the claims say: its key, nodes and the claim that typed its `formed` event."""
    key: str
    nodes: tuple
    type_claim: str


def short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def detector_iri(name: str) -> str:
    return f"urn:kk:detector:{name}"


def match_iri(name: str, key: str) -> str:
    return f"urn:kk:match:{name}:{short(key)}"


def event_iri(name: str, key: str, kind: str, cursor: int) -> str:
    return f"urn:kk:event:{name}:{short(key)}:{kind.rsplit(':', 1)[1]}:{cursor}"


def diff(current: list[Match], stored: dict[str, Stored], anchors: list | None) -> tuple[list[Match], list[Stored]]:
    """(formed, dissolved). With anchors, only stored matches naming one of them can dissolve: the query
    looked at those entities only. A match found now and not stored forms, anchored or not."""
    now = {m.key: m for m in current}
    formed = [m for k, m in sorted(now.items()) if k not in stored]
    near = None if anchors is None else set(anchors)
    dissolved = [s for k, s in sorted(stored.items()) if k not in now and (near is None or near & set(s.nodes))]
    return formed, dissolved


def _op(s, p, o, premises):
    obj = {"entity": o} if not isinstance(o, dict) else {"value": o}
    return {"op": "add_claim", "subject": s, "predicate": p, "object": obj, "evidence_type": "inferred",
            "derived_from": list(premises)}


def event_ops(name: str, formed: list[Match], dissolved: list[Stored], cursor: int, now: str,
              known: set[str]) -> list[dict]:
    """The add_claim ops that record the events. `known`: match IRIs that already have their own claims."""
    det, ops = detector_iri(name), []
    for m in formed:
        if not m.claims:
            raise ValueError(f"detector {name}: match {m.key!r} names no claims it was made from")
        who, ev = match_iri(name, m.key), event_iri(name, m.key, FORMED, cursor)
        if who not in known:
            ops += [_op(who, RDF_TYPE, ENTITY, m.claims), _op(who, DCT_TYPE, det, m.claims),
                    _op(who, LABEL, {"@value": m.key}, m.claims)]
        ops += _event(ev, FORMED, det, who, m.nodes, now, m.claims)
    for s in dissolved:
        who, ev = match_iri(name, s.key), event_iri(name, s.key, DISSOLVED, cursor)
        ops += _event(ev, DISSOLVED, det, who, s.nodes, now, [s.type_claim])
    return ops


def _event(ev, kind, det, who, nodes, now, premises):
    ops = [_op(ev, RDF_TYPE, ACTIVITY, premises), _op(ev, DCT_TYPE, kind, premises),
           _op(ev, INSTRUMENT, det, premises), _op(ev, USED, who, premises),
           _op(ev, PUBLISHED, {"@value": now, "@type": XSD_DATETIME}, premises)]
    return ops + [_op(ev, RELATION, n, premises) for n in sorted(nodes)]


def change_set_key(name: str, cursor_from, cursor_to: int, formed: list[Match], dissolved: list[Stored]) -> str:
    """The same range with the same outcome is the same change set (a retry is a no-op); the time is not in it."""
    what = [["f", m.key] for m in formed] + [["d", s.key] for s in dissolved]
    return f"detectors:{name}|{cursor_from}-{cursor_to}|{short(repr(what))}"

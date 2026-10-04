"""Compare a produced graph with a fixture's expected graph.

Entities match on (type, kind, normalised name); edges on (kernel edge, kind, normalised
endpoint names). Precision and recall are reported for entities and edges separately.
Attributes listed in the expected graph (belief status, windows, status) are checked on
matched items and reported as attribute accuracy.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

Key = tuple[str | None, ...]


def norm(name: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (name or "").lower()).strip()


@dataclass
class Score:
    expected: int
    produced: int
    matched: int
    missing: list[Key] = field(default_factory=list)
    unexpected: list[Key] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.matched / self.produced if self.produced else 1.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


@dataclass
class Comparison:
    entities: Score
    edges: Score
    attribute_checks: int
    attribute_errors: list[str]
    unresolved_expected: int
    unresolved_produced: int

    @property
    def attribute_accuracy(self) -> float:
        if not self.attribute_checks:
            return 1.0
        return 1 - len(self.attribute_errors) / self.attribute_checks


def produced_graph(dsn: str) -> dict[str, Any]:
    """The graph in a kernel database, without the machine agents that run the profiles."""
    with psycopg.connect(dsn) as conn:
        entities = conn.execute(
            "SELECT type, kind, name, status, belief_status FROM kernel.nodes "
            "WHERE NOT (type = 'Agent' AND kind = 'machine')"
        ).fetchall()
        edges = conn.execute(
            "SELECT e.edge, e.kind, f.name, t.name, e.valid_from, e.valid_to, e.belief_status "
            "FROM kernel.edges e JOIN kernel.nodes f ON f.id = e.from_id "
            "JOIN kernel.nodes t ON t.id = e.to_id"
        ).fetchall()
        unresolved = conn.execute(
            "SELECT count(*) FROM kernel.claims WHERE resolution = 'unresolved'"
        ).fetchone()
    return {
        "entities": [
            {"type": r[0], "kind": r[1], "name": r[2], "status": r[3], "belief_status": r[4]}
            for r in entities
        ],
        "edges": [
            {
                "edge": r[0],
                "kind": r[1],
                "from": r[2],
                "to": r[3],
                "valid_from": _day(r[4]),
                "valid_to": _day(r[5]),
                "belief_status": r[6],
            }
            for r in edges
        ],
        "unresolved_claims": unresolved[0] if unresolved else 0,
    }


def _day(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value else None


def entity_key(e: dict[str, Any]) -> Key:
    return (e["type"], e["kind"], norm(e["name"]))


def edge_key(e: dict[str, Any]) -> Key:
    return (e["edge"], e.get("kind"), norm(e["from"]), norm(e["to"]))


def _score(expected: list[Key], produced: list[Key]) -> Score:
    want, got = Counter(expected), Counter(produced)
    matched = sum((want & got).values())
    return Score(
        expected=len(expected),
        produced=len(produced),
        matched=matched,
        missing=sorted((want - got).elements(), key=str),
        unexpected=sorted((got - want).elements(), key=str),
    )


def compare(expected: dict[str, Any], produced: dict[str, Any]) -> Comparison:
    entities = _score(
        [entity_key(e) for e in expected.get("entities", [])], [entity_key(e) for e in produced["entities"]]
    )
    edges = _score([edge_key(e) for e in expected.get("edges", [])], [edge_key(e) for e in produced["edges"]])
    checks = 0
    errors: list[str] = []
    for kind, keyf, attrs in (
        ("entities", entity_key, ("status", "belief_status")),
        ("edges", edge_key, ("valid_from", "valid_to", "belief_status")),
    ):
        index = {keyf(p): p for p in produced[kind]}
        for e in expected.get(kind, []):
            found = index.get(keyf(e))
            if found is None:
                continue
            for attr in attrs:
                if attr in e:
                    checks += 1
                    want = None if e[attr] is None else str(e[attr])
                    if found.get(attr) != want:
                        errors.append(f"{kind} {keyf(e)}: {attr} is {found.get(attr)!r}, expected {want!r}")
    return Comparison(
        entities=entities,
        edges=edges,
        attribute_checks=checks,
        attribute_errors=errors,
        unresolved_expected=int(expected.get("unresolved_claims", 0)),
        unresolved_produced=int(produced["unresolved_claims"]),
    )

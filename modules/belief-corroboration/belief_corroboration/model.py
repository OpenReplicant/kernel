"""Corroboration counting, v0. Pure function, standard library only.

For a claim x:
  support   = non-deprecated claims with x's statement key (x included)
  conflicts = non-deprecated claims on x's subject and a single-valued (owl:FunctionalProperty)
              predicate, with a different object and a validity window overlapping x's
  S, C      = the number of *independent* groups in each set: two claims are dependent when they
              share a source (artifact hash or source URI) or were made by the same agent, so a
              copied source or a repeated assertion adds nothing
  belief    = S / (S + C + 1)

Claims whose validity windows don't overlap are changes over time, not conflicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MODEL = "urn:kk:module:belief-corroboration-v0"


@dataclass(frozen=True)
class Claim:
    id: str
    key: str                     # statement key
    subject: str
    predicate: str
    object: str                  # canonical JSON of the object (entity or value)
    agent: str
    sources: frozenset           # artifact hashes and source URIs
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    deprecated: bool = False


def overlap(a: Claim, b: Claim) -> bool:
    """Half-open validity windows [from, to); a missing bound is unbounded."""
    def ts(x: datetime | None, unbounded: float) -> float:
        return x.timestamp() if x else unbounded
    inf = float("inf")
    return ts(a.valid_from, -inf) < ts(b.valid_to, inf) and ts(b.valid_from, -inf) < ts(a.valid_to, inf)


def independent_groups(claims: list[Claim]) -> int:
    """Connected components where an edge means: shared source or same agent."""
    parent = list(range(len(claims)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(claims):
        for j in range(i + 1, len(claims)):
            b = claims[j]
            if a.agent == b.agent or a.sources & b.sources:
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(claims))})


def score(claims: list[Claim], functional: set[str]) -> dict[str, tuple[float, dict]]:
    """Belief for every non-deprecated claim: {claim id: (value, explanation)}."""
    live = [c for c in claims if not c.deprecated]
    by_key: dict[str, list[Claim]] = {}
    by_slot: dict[tuple[str, str], list[Claim]] = {}
    for c in live:
        by_key.setdefault(c.key, []).append(c)
        if c.predicate in functional:
            by_slot.setdefault((c.subject, c.predicate), []).append(c)
    out = {}
    for c in live:
        s = independent_groups(by_key[c.key])
        rivals = [r for r in by_slot.get((c.subject, c.predicate), []) if r.object != c.object and overlap(c, r)]
        k = independent_groups(rivals) if rivals else 0
        out[c.id] = (round(s / (s + k + 1), 4),
                     {"model": MODEL, "independent_sources": s, "conflicts": k,
                      "conflicting_claims": sorted(r.id for r in rivals)[:20]})
    return out

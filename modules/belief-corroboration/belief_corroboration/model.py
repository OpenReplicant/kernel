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

Evidence (D-62): when works bear on an entity with cito:supports or cito:confirms (for) and
cito:refutes or cito:disputes (against), the entity's rdfs:label claim carries the belief that the
labelled statement holds (a proposition `urn:kk:proposition:<hash>` is named by its text, so its label
cannot be wrong otherwise):
  F, A      = the number of distinct works (the evidence claims' subjects) for and against; a work
              counted on both sides counts on neither; extractor agents do not make works dependent
  belief    = (F + 1) / (F + A + 2)     (no evidence: 0.5; one work for: 0.67; one against: 0.33)

Verification (D-67): a claim that a verifier refuted (its quote is not in its source, say) keeps at most
REFUTED_CAP, whatever its corroboration: a verifier is never the claim's producer (invariant 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MODEL = "urn:kk:module:belief-corroboration-v0"
REFUTED_CAP = 0.1


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
    entity: str | None = None     # the object entity's IRI, if the object is an entity
    refuted_by: tuple = ()        # verifier modules that refuted the claim


CITO = "http://purl.org/spar/cito/"
FOR = {CITO + "supports", CITO + "confirms"}
AGAINST = {CITO + "refutes", CITO + "disputes"}
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


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
    out = corroboration(claims, functional)
    for c in claims:
        if c.id in out and c.refuted_by:
            value, why = out[c.id]
            out[c.id] = (min(value, REFUTED_CAP), {**why, "refuted_by": sorted(c.refuted_by), "cap": REFUTED_CAP})
    return out


def corroboration(claims: list[Claim], functional: set[str]) -> dict[str, tuple[float, dict]]:
    live = [c for c in claims if not c.deprecated]
    by_key: dict[str, list[Claim]] = {}
    by_slot: dict[tuple[str, str], list[Claim]] = {}
    for c in live:
        by_key.setdefault(c.key, []).append(c)
        if c.predicate in functional:
            by_slot.setdefault((c.subject, c.predicate), []).append(c)
    stances: dict[str, dict[str, set]] = {}     # entity -> work -> stances
    for c in live:
        if c.entity and (c.predicate in FOR or c.predicate in AGAINST):
            stances.setdefault(c.entity, {}).setdefault(c.subject, set()).add(c.predicate in FOR)
    out = {}
    for c in live:
        if c.predicate == LABEL and c.subject in stances:
            works = stances[c.subject]
            f = sorted(w for w, st in works.items() if st == {True})
            a = sorted(w for w, st in works.items() if st == {False})
            out[c.id] = (round((len(f) + 1) / (len(f) + len(a) + 2), 4),
                         {"model": MODEL, "rule": "evidence", "works_for": len(f), "works_against": len(a),
                          "for": f[:20], "against": a[:20]})
            continue
        s = independent_groups(by_key[c.key])
        rivals = [r for r in by_slot.get((c.subject, c.predicate), []) if r.object != c.object and overlap(c, r)]
        k = independent_groups(rivals) if rivals else 0
        out[c.id] = (round(s / (s + k + 1), 4),
                     {"model": MODEL, "independent_sources": s, "conflicts": k,
                      "conflicting_claims": sorted(r.id for r in rivals)[:20]})
    return out

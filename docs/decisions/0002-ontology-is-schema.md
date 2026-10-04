# 0002. The ontology is schema, loaded by SQL; rules are checked from data

Date: 2026-10-04 · Status: accepted

## Context

The design has packs declare rules that "the installer compiles into checks", and puts
the pack installer in Phase 2. Phase 1 still needs one reference pack.

## Decision

- Ontology tables (`kernel.node_types`, `edge_types`, `kinds`, `namespaces`,
  `edge_kinds`, `statuses`, `rules`) are schema, not data. They are written by SQL files
  at deploy time, never by `kernel.write`, and every entry needs a label and a
  description (CHECK constraints refuse empty ones).
- Node types and kernel edges are fixed by CHECK constraints; an edge specialisation may
  not shadow a kernel edge (or `violates`).
- `kernel.write` interprets rules generically from `kernel.rules` by category (types,
  domain_range, cardinality, time, identity, provenance). Nothing is compiled yet.
- The reference pack `packs/bpm-reference` ships its ontology as `sql/10_ontology.sql`,
  applied right after `kernel/sql` by `db/initdb` and `kernel.admin`. No fetching,
  hashing, registry or approval step exists in Phase 1.

## Consequences

Changing a rule changes future checks only: the log records each write's outcome
(resolved ops and recorded conflicts), so replay never re-validates and stays exact. The
Phase 2 installer replaces the manual SQL step and may compile rules for speed.

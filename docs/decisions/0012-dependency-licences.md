# 0012. Dependency licences

Date: 2026-10-04 · Status: accepted

## Context

Later phases add adapters (document parsing, paper sources, process mining, duplicate
sweeps), and the strongest libraries in some areas are copyleft or carry field-of-use
terms. The kernel is meant to be usable commercially. The survey is in
[docs/adapters.md](../adapters.md).

## Decision

- Imported into the gateway, the kernel or a shipped container: permissive licences
  (MIT, BSD, Apache-2.0, ISC, PostgreSQL). LGPL is acceptable unmodified and dynamically
  linked; psycopg 3 already is LGPL-3.0.
- GPL and AGPL code is not imported or shipped. Where only such a tool exists, it runs as
  a separate service the operator installs, after legal review, or is replaced.
- Model weights count as dependencies: their licence is checked like code, and
  revenue-capped or use-restricted weights are not shipped.
- External APIs are checked for commercial terms before a pack depends on them.

## Consequences

PyMuPDF, pm4py, Zingg, Marker and MinerU are out as built-in components; Docling,
pdfplumber, GROBID (as a service), Splink and procrastinate cover the same needs. New
dependencies name their licence in the pull request that adds them.

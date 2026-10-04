# World Model Kernel

A lightweight world model any agent can plug into. Agents write **claims** plus the
**graph operations** they justify; the kernel validates them against ontology rules and
commits the claim, the log entry and the graph change in one Postgres transaction. The
log is append-only and is the source of truth; the graph is its projection.

Full design: `docs/design-v1.md` (export of "World Model Kernel — Design v1.0").
Read the relevant section before changing anything architectural.

## Current phase: Phase 2, research track (approved 2026-10-04)

Phase 1 (the MVP) is complete: `kernel/sql/`, the gateway's seven tools, `skills/core/`
(plus `skills/interview/`), `docker-compose.yml`, `evals/`, `profiles/`. Keep all of it
working; its invariants and tests still apply.

Research is the first product (ADR 0013). In scope, in this order:

1. `packs/research/` — the research pack: ontology, skill, evidence queries, fixtures.
2. A paper-source pack container (arXiv, OpenAlex, Crossref) exposed as an MCP server.
   It never gets database credentials. Libraries per `docs/adapters.md`.
3. A live research eval: a real model maps papers through the eval profile.
4. The parser container (full text, GROBID or Docling) and workers for bulk extraction,
   once the live eval shows extraction quality. Workers need an ADR first on how job
   queues fit invariant 1.

**Out of scope for now** (do not build, do not stub): observer runs, pack installer and
registry, workflows, ops loop, vital signs, concept formation, habit formation, the BPM
product. If a task seems to need one of these, stop and ask.

## Stack

- Python 3.12, dependencies managed with `uv`
- Official MCP Python SDK; `psycopg` 3 for Postgres
- Postgres 18 with Apache AGE, pgvector, pg_trgm
- OpenTelemetry Python SDK
- `pytest` for tests, `ruff` for lint and format

## Commands

Create these as Makefile targets early; keep them working.

- `make up` — start the stack
- `make down` — stop it and remove volumes
- `make test` — unit, SQL and regression tests
- `make replay` — rebuild the graph from the log and diff against the live graph
- `make eval` — run the eval fixtures through the eval profile
- `make lint` — ruff check and format check

## Invariants — never violate these

1. **One write path.** Only `kernel.write`, `kernel.ingest_source` and `kernel.cite`
   change data. Gateway code never issues INSERT, UPDATE or DELETE directly. Bulk
   loading is many payloads through `kernel.write`, never a direct import.
2. **The log is append-only.** No UPDATE or DELETE on log tables, enforced by
   permissions and a trigger. There is no delete operation; retraction is a new
   assertion with opposite polarity, or a `supersedes`.
3. **Projection is deterministic.** Triggers and projection functions make no network
   calls, never call `now()` (use the entry's `recorded_at`), use no randomness.
   Replaying the log must reproduce the graph exactly.
4. **The log stores operations in kernel vocabulary.** Never Cypher, never SQL, never
   an instruction to call a model.
5. **No model calls inside the database or inside a transaction.**
6. **Belief is a pure function of assertions.** Each source counts once. No time decay.
   Conflicts follow the kernel's policy: same source newer supersedes; a different
   source makes the fact contested. Never resolve a conflict by overwriting.
7. **Kernel node types and edges are fixed.** Four node types (Entity, Agent, Claim,
   Event) and the kernel edge list in the design doc. Packs specialize; they never add
   kernel-level types or edges.
8. **Rejections are RFC 9457 problem documents** naming the broken rule, plus duplicate
   candidates or nearest allowed kinds where relevant.
9. **No personal data in telemetry.** Spans and app logs carry IDs, never claim text,
   source content or message content.
10. **The kernel is passive.** It records and answers. Nothing in this repo acts on
    the outside world.

## The write payload

```json
{"claim": {"text": "...", "source": "<chunk id>", "basis": "reported",
           "modality": "descriptive"},
 "read_at_offset": 48210, "trace_id": "...",
 "ops": [{"op": "assert", "edge": "implements", "from": "agt_sam",
          "to": "role_approver", "valid_from": "2026-03-01"}]}
```

Operations: `create`, `assert`, `link`, `unlink`, `promote`, `transition`, `redact`.

`kernel.write` steps, in order: take the append lock and assign the next offset →
validate every op against ontology rules → reject if any touched node changed after
`read_at_offset` → run the resolution cascade on every `create` → append the log entry
and apply graph changes → update belief.

A claim that maps to no ontology term is stored as an **unresolved claim** (text and
provenance, no ops). Never drop it.

## Conventions

- **SQL:** kernel objects live in schema `kernel`; files in `kernel/sql/` are numbered
  and applied in order (`00_extensions.sql`, `10_log.sql`, …). Every function has a
  comment stating its contract.
- **Python:** fully typed; small modules; no ORM.
- **Errors:** build problem documents in one place (`gateway/problems.py`).
- **OTel:** all span and attribute names live in `gateway/otel.py`. The GenAI and MCP
  semantic conventions are not yet stable; never scatter names through the code.
- **IDs:** ULID or UUIDv7, assigned when the entry is logged, never by the graph.
- **Two clocks:** `valid_from`/`valid_to` (true in the world) and `recorded_at` plus
  offset (when learned). Keep them separate in every query and test.
- **Decisions:** anything that changes the design gets a short ADR in `docs/decisions/`.

## Testing

- Every invariant above has at least one test.
- `make replay` runs in CI on every change; a non-empty diff fails the build.
- Regression tests for known failure modes from Graphiti's history:
  - invalidation must stay scoped to the same edge type and endpoints;
  - non-overlapping validity windows must never collapse into one edge;
  - bulk writes must pass the same rule checks as single writes.
- Evals compare the produced graph with the expected graph per fixture. Report
  precision and recall for entities and edges separately.

## Build order for Phase 1

1. Compose stack with Postgres and extensions; `make up` works.
2. Log, claims, assertions and graph tables; append-only enforcement; roles.
3. `kernel.write` with ops, rule checks, stale-read check and offset lock.
4. Resolution cascade: identity keys → normalized exact → pg_trgm → pgvector.
5. Belief triggers and contested status.
6. `kernel.ingest_source` (text and Markdown) and `kernel.cite`.
7. Gateway with the seven tools, RFC 9457 errors and OTel spans.
8. Core skill.
9. Replay test, one reference pack, eval fixtures, eval profile in CI.
10. Interactive profile.

## Glossary

- **Claim:** a sourced statement; becomes the assertion record for its ops.
- **Assertion:** source, agent, time, confidence, polarity, modality and basis backing
  an edge or claim.
- **Basis:** observed, reported or inferred.
- **Modality:** descriptive, predictive, normative, proposed or hypothetical.
- **Belief status:** accepted, contested, rejected or unknown.
- **Unresolved claim:** a claim kept without graph ops until it can be placed.
- **Schema slice:** the kinds, edges and rules most relevant to one passage.

## When unsure

Check the design doc. Prefer the smallest change that satisfies the invariants. Do not
expand scope beyond the current phase without asking.

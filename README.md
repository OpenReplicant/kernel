# World Model Kernel

A lightweight world model any agent can plug into. Agents write **claims** plus the
**graph operations** they justify; the kernel validates them against ontology rules and
commits the claim, the log entry and the graph change in one Postgres transaction. The
log is append-only and is the source of truth; the graph is its projection, and replaying
the log rebuilds it exactly.

Every fact keeps who said it and where, when it was true in the world and when the
kernel learned it, and whether credible sources agree (accepted) or disagree (contested).

Design: [`docs/design-v1.md`](docs/design-v1.md). Decisions taken while building
Phase 1: [`docs/decisions/`](docs/decisions/). Libraries to adopt for later adapters:
[`docs/adapters.md`](docs/adapters.md).

## Quick start

```sh
make up        # Postgres 18 (AGE, pgvector, pg_trgm) + the MCP gateway on :8000
make seed      # optional: write the eval fixtures (two BPM, one research) through the gateway
make replay    # rebuild the graph from the log and diff it against the live graph
```

Connect any MCP harness to `http://localhost:8000/mcp` (streamable HTTP; published on
localhost only, since the stack has no authentication) and give it the core skill in
[`skills/core/`](skills/core/SKILL.md), plus [`skills/interview/`](skills/interview/SKILL.md)
when a person is the source. For a stdio harness, run the gateway with
`WMK_TRANSPORT=stdio uv run wmk-gateway`. `make up-otel` adds an OpenTelemetry
Collector and Jaeger (http://localhost:16686).

For research, `make up-research` adds the research pack's paper-source server on
`http://localhost:8001/mcp` (arXiv, OpenAlex, Crossref): connect it beside the gateway and
give the harness [`packs/research/`](packs/research/SKILL.md) as a skill. Set
`WMK_PAPERS_MAILTO` to a contact email for the APIs' polite pools, and
`WMK_PAPERS_OPENALEX_API_KEY` (free) to search OpenAlex.

## The seven tools

| Tool | Tier | Does |
| --- | --- | --- |
| `write` | Write | Submits a claim and its operations to `kernel.write` |
| `lookup_entities` | Read | Ranked resolution candidates: identity keys, normalised names, trigrams, embeddings |
| `get_schema_slice` | Read | The kinds, edges and rules most relevant to a passage |
| `query_graph` | Read | Read-only Cypher, with belief status, `valid_at` and `known_at_offset` |
| `query_log` | Read | Log entries by source, agent, node, edge, time or offset |
| `ingest_source` | Write | Stores text or Markdown, chunks it with spans, skips known content |
| `cite` | Write | Records which assertions each answer sentence relied on |

A write:

```json
{"claim": {"text": "Sam took over invoice approval from Dana in March",
           "source": "chk_01K..._0002", "basis": "reported", "modality": "descriptive"},
 "read_at_offset": 41,
 "ops": [{"op": "assert", "edge": "implements", "from": "agt_01K...", "to": "ent_01K...",
          "valid_from": "2026-03-01"},
         {"op": "assert", "edge_id": "edg_01K...", "valid_to": "2026-03-01"}]}
```

Operations: `create`, `assert`, `link`, `unlink`, `promote`, `transition`, `redact`.
Rejections are RFC 9457 problem documents naming the broken rule:

```json
{"type": "urn:wmk:rule:cardinality", "title": "Single-valued edge conflict", "status": 409,
 "detail": "ent_01K... already has implements from agt_01K... for 2026-03-01T00:00:00Z onward; ...",
 "rule": "bpm.approver_cardinality", "candidates": [], "conflicting_edges": ["edg_01K..."]}
```

## Layout

| Path | Contents |
| --- | --- |
| `kernel/sql/` | The kernel, applied in order: log, claims, assertions, sources, chunks, cites, ontology, graph and AGE mirror, belief, resolution, projection, `kernel.write`, `kernel.ingest_source`, `kernel.cite`, read helpers, roles |
| `gateway/` | The MCP server (official Python SDK): tools, RFC 9457 problems (`problems.py`), OTel names (`otel.py`) |
| `skills/` | Agent Skills: `core` (read, extract, write, cite) and `interview` (consent, gap queries, follow-ups) |
| `packs/` | Self-contained packs, each with its ontology (`schema.yaml`, `rules.yaml`), skill, tests, fixtures and servers ([writing a pack](docs/packs.md)): `research` (papers, per-paper findings, evidence queries, a paper-source server; [ADR 0013](docs/decisions/0013-research-findings-are-claims.md)) and `bpm-reference` (the kernel's toy business-process test pack) |
| `profiles/` | ACP profiles `interactive.yaml` and `eval.yaml` |
| `evals/` | Fixtures with expected graphs, the resolution set, the eval runners, the replay check, the seeder |
| `tests/` | Unit, SQL, invariant and regression tests; the shared test kit is `kernel/testing.py` |
| `db/`, `docker-compose.yml` | The database image and the stack |

## Development

Python 3.12 with [uv](https://docs.astral.sh/uv/); Docker for the database.

| Command | Does |
| --- | --- |
| `make up` / `make down` | Start the stack / stop it and remove volumes |
| `make test` | Unit, SQL and regression tests (starts the database) |
| `make eval` | Every fixture through the eval profile (precision and recall for entities and edges), then the resolution set (auto-band precision and recall, candidate recall, clean new names) |
| `make replay` | Rebuild `$WMK_DATABASE` (default `wmk`) from its log and diff; a non-empty diff fails |
| `make live` | A real harness on a fresh stack: headless Claude Code, the skills and the gateway map a document and a five-turn interview (`northwind`, about US$3), or with `SCENARIO=research` on `WMK_PROFILE=eval`, three papers scored against the research fixture (about US$2); each answers with citations, then replays. Needs the `claude` CLI and model access; not in CI |
| `make papers-smoke` | One live lookup per paper source; needs network access to arXiv, Crossref and OpenAlex |
| `uv run python -m kernel.packs check` | Validate every pack's manifests ([ADR 0015](docs/decisions/0015-packs-declare-their-ontology.md)) |
| `make lint` | `ruff check` and `ruff format --check` |

Tests and evals create throwaway databases through `WMK_ADMIN_DSN` (default
`postgresql://postgres:postgres@localhost:5432/postgres`).

### Configuration

| Variable | Default | Used by |
| --- | --- | --- |
| `WMK_DB_PASSWORD`, `WMK_WRITER_PASSWORD`, `WMK_READER_PASSWORD` | `postgres`, `writer`, `reader` | compose |
| `WMK_PROFILE` | `interactive` | compose: which profile the gateway runs |
| `WMK_PACKS` | every pack | compose and `kernel.packs install`: which packs the database gets |
| `WMK_WRITER_DSN`, `WMK_READER_DSN` | set by compose | gateway |
| `WMK_TRANSPORT`, `WMK_HOST`, `WMK_PORT` | `streamable-http`, `0.0.0.0`, `8000` | gateway |
| `WMK_EMBEDDING_URL`, `WMK_EMBEDDING_MODEL`, `WMK_EMBEDDING_API_KEY` | unset | gateway: optional OpenAI-compatible embeddings |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | gateway: export spans and metrics |
| `WMK_PAPERS_MAILTO`, `WMK_PAPERS_OPENALEX_API_KEY` | unset | papers: contact email for the APIs; OpenAlex search |

## Guarantees

- One write path: the gateway's writer role may only execute `kernel.write`,
  `kernel.ingest_source` and `kernel.cite`; its reader role may only select.
- The log tables refuse UPDATE, DELETE and TRUNCATE for every role.
- Projection makes no clock, random or network calls; CI replays the log and diffs.
- No model calls in the database or in a transaction: embeddings come from the gateway.
- Belief is a pure function of assertions: each source counts once, no decay, conflicts
  between sources are shown as contested, never overwritten.
- Telemetry carries IDs, never claim text, source content or message content.

## Current limits

Research is the first product pack: papers are found with the paper-source server and
mapped from their abstracts by any MCP harness following the core and research skills.
Not yet built: the parser container for full text, workers, observer runs, and the pack
registry (packs are installed from this repository by the stack's `packs` service; pack
servers run beside the gateway,
[ADR 0014](docs/decisions/0014-pack-servers-run-beside-the-gateway.md)). Redaction masks the graph and read
paths but does not yet erase source content
([ADR 0006](docs/decisions/0006-redaction-in-phase-1.md)). CI drives the eval profile with
scripted extraction ([ADR 0008](docs/decisions/0008-eval-profile-runs-scripted-extraction.md));
`make live` runs a real model.

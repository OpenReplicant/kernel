# Adapters: adopt before building

A survey of maintained open-source libraries for the adapters the design names, so later
phases wrap existing work instead of rebuilding it. Licences were read from each project's
LICENSE file and PyPI metadata, and release dates from PyPI or release feeds, on 2026-10-04.
Items marked † rest on secondary sources; re-check them before adopting. Licence posture:
[ADR 0012](decisions/0012-dependency-licences.md).

Adopted so far: `arxiv`, `pyalex` and `habanero` in the research pack's paper-source server
([ADR 0014](decisions/0014-pack-servers-run-beside-the-gateway.md)). habanero uses `httpx2`,
the continuation of httpx under Pydantic's stewardship (BSD-3).

## Recommendations

| Area | Adopt | Still ours to build |
| --- | --- | --- |
| Document parsing (parser container) | `docling` / `docling-serve` (MIT; LF AI & Data): PDF, DOCX, PPTX, XLSX, HTML with page, bbox and per-item character spans. Light path for born-digital PDFs: `pdfplumber` (MIT) or `pypdf` (BSD-3). Papers: GROBID (Apache-2.0, JVM service) | Canonical text with document-level offsets, chunk ids from (source hash, page, span), parser version in source metadata, OCR policy |
| Structured data | `duckdb` (MIT) to read CSV, Parquet, Excel and Postgres; Frictionless Table Schema (`frictionless` v5, MIT) to declare keys and foreign keys. Mapping files in a YARRRML subset; `morph-kgc` (Apache-2.0) only as a test oracle | Subset parser, compiler from mappings to `create`/`assert` payloads (basis `observed`), row provenance (file hash + row as chunk), batching through `kernel.write` |
| PROV-O and SKOS export | `rdflib` (BSD-3), `pyshacl` (Apache-2.0) to validate exports in CI | The mapping: claim as `prov:Entity`, log entry as `prov:Activity`, agent as `prov:Agent`, chunk via `wasDerivedFrom`, `recorded_at` as `generatedAtTime`, validity kept separate; kinds as `skos:Concept`, packs as schemes; sorted N-Triples for diffable exports |
| Paper sources (research pack) | `arxiv` (MIT), `pyalex` (MIT; OpenAlex needs an API key since 2026-02), `habanero` (MIT; Crossref limits tightened 2025-12) | Rate limiters, caching, DOI as identity key, metadata as `reported` claims, per-paper licence capture, the pack's MCP tools |
| Duplicate sweeps (`same_as` proposals) | `splink` (MIT) on DuckDB, reading the kernel through a read-only role | Blocking rules per kind, proposals written as `inferred` claims through `kernel.write`, threshold policy, evals |
| Job queue (workers) | `procrastinate` (MIT; psycopg 3, LISTEN/NOTIFY) in its own schema | An ADR first (see below), idempotency keyed on log offset |
| Observer runs | `agent-client-protocol` (Apache-2.0; official ACP SDK, pydantic only). 1.0 is in release candidate | Headless client: spawn the harness, answer permission requests by profile, log IDs only |
| Pack MCP servers behind the gateway | `fastmcp` 4 (Apache-2.0) has `mount(namespace=...)` and proxies; the official SDK 2.x has neither | Reconcile fastmcp's span names with `gateway/otel.py`; tool names come out as `research_search_arxiv` (underscore), not dotted |
| BPM pack | `process_mining` / `r4pm` (MIT or Apache-2.0) for XES and OCEL 2.0 I/O; SpiffWorkflow (LGPL-3.0) as a BPMN parser only | BPMN to kinds and edges; OCEL objects and events as observed claims; any conformance analytics |
| Ops pack | `cloudevents` (Apache-2.0); `jsonschema` (MIT) to validate Serverless Workflow 1.0 and CACAO 2.0 documents against their published schemas | OpenSLO models (no Python SDK; the `oslo` CLI validates) |
| Embeddings without a hosted API | Hugging Face TEI (Apache-2.0; OpenAI-compatible `/v1/embeddings`, works with `WMK_EMBEDDING_URL` as is); `fastembed` (Apache-2.0, ONNX, no torch) inside a worker; Ollama for development | Per-model licence checks |
| Chunking, Markdown, dates | `semchunk` (MIT; exact `(start, end)` offsets), `markdown-it-py` (MIT; line maps on block tokens), `dateparser` (BSD-3; always pass `RELATIVE_BASE` = the source's date, its default is now) | Line-to-character offset index for Markdown |

## Avoid

| Library | Why |
| --- | --- |
| PyMuPDF, `pymupdf4llm` | AGPL-3.0 or a commercial licence |
| `pm4py` | AGPL-3.0 or a commercial licence; process mining in-process needs another route |
| Zingg | AGPL-3.0 |
| `bpmn-python` | GPL-3.0, unmaintained since 2017 |
| Marker | Code Apache-2.0, but model weights are free only under US$5M funding or revenue |
| MinerU | Custom licence: commercial terms above a size threshold, mandatory attribution for online services |
| `unstructured[all-docs]` | Bundles pandoc (GPL-2.0+); heavy core dependencies; no source character offsets |
| Camunda 8 / Zeebe | Camunda License 1.0: non-production use only without a paid licence (Operaton, Apache-2.0, is the open Camunda 7 fork) |
| Semantic Scholar API for commercial use | Needs an agreement with AI2† |
| `dedupe`, `recordlinkage`, `skosify`, `unpywall`, `serverlessworkflow-sdk` | Unmaintained or tied to old spec versions |
| Cypher parsers (`libcypher-parser`, `opencypher`) | Dead; see below |

## What the gateway keeps hand-rolled, and why

- **Read-only Cypher.** No maintained Python openCypher parser exists. The database is the
  real guard: `query_graph` runs as `kernel_reader` (SELECT only) in a `READ ONLY`
  transaction with a statement timeout, and `tests/test_invariants.py` checks that write
  clauses fail there even past the lexical check, which stays as a first, clearer error.
- **RFC 9457 documents.** The available package targets HTTP frameworks; `gateway/problems.py`
  is small and owns the kernel's problem types.
- **IDs.** Postgres 18's `uuidv7()` inside `kernel.write`, encoded as ULID text in SQL;
  projection and replay read stored ids only.
- **Telemetry scrubbing.** The attribute allow-list in `gateway/otel.py`; in a deployment,
  the OpenTelemetry Collector's `redaction` processor (`allowed_keys`) fails closed as a
  second layer. Presidio needs spaCy models and is not needed for ID-only telemetry.

## Invariant questions to settle before building

- **Job queues write rows.** Enqueueing is an INSERT outside the three write functions
  (invariant 1). Either declare job tables infrastructure outside the kernel's data, or
  enqueue from a trigger on log append. Needs an ADR when workers start.
- **Splink's Postgres backend creates working tables.** Run it on DuckDB against a
  read-only attachment instead; its proposals enter through `kernel.write`.
- **Relative dates.** Any date parser used in extraction resolves against the source's own
  date, never the clock (invariant 3 applies to projection; the skill rule applies here).

## Could not verify

Hugging Face was unreachable, so model-weight licences (Docling's layout and table models,
embedding models) are from secondary sources. Also unverified: Semantic Scholar's
commercial terms, OpenAlex's exact free tier, Europe PMC rate limits, Docling's
`charspan` semantics for non-PDF formats, and whether fastmcp can emit dotted tool names.

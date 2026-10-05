# 0024. Extraction workers: existing parsers, scripted plumbing, the model only writes claims

Date: 2026-10-04, revised 2026-10-05 · Status: proposed (design only; nothing here is built until
reviewed, and workers wait for the annotated eval's floors)

## Context

The research track plans workers for bulk extraction once a live eval shows extraction
quality, and CLAUDE.md asks for an ADR first on how job queues fit invariant 1 (one write
path). The annotated-corpus eval (`make annotated`) now measures extraction against
independent annotations.

The first version of this ADR proposed a `jobs` schema, a `wmk_jobs` role, workers claiming
jobs with `SKIP LOCKED`, and a sweep for abandoned runs. The review asked what already
exists. The survey:

- **Parsers already run as MCP servers.**
  - docling-mcp (MIT, from the Docling project) converts PDF, Office documents, HTML and
    audio. Its layout models come from Hugging Face.
  - markitdown-mcp (Microsoft) has one tool, `convert_to_markdown(uri)`. It is lighter and
    has no layout analysis.
  - For papers, the papers server's `get_full_text` runs GROBID (ADR 0025).
- **Extraction servers write elsewhere.** Graphiti's MCP server, and memory servers like it,
  extract facts and write them to their own graph store. Used here, they would be a second
  write path with no quotes, no ontology rules and no belief.
- **LangExtract** (Google, Apache-2.0) is the closest library. It chunks long documents,
  runs in parallel and in several passes, and aligns every extraction to a character span
  of its source, as the kernel's quote rule does. It has no official MCP server. Its
  providers are Gemini, OpenAI and Ollama. It extracts entities and attributes, not claims
  with graph operations.
- **MCP's own primitives.**
  - Tasks (spec 2025-11-25, experimental) let a long call return a handle the client polls.
  - Sampling would let a server use the client's model instead of its own API key. Claude
    Code does not support it (anthropics/claude-code#1785 is open).
- **The worker harness exists.** `make annotated` already runs headless Claude Code, one
  session per item, with its state in a JSONL manifest.

The survey also found a gap in what is built. Any parser behind MCP returns the document to
the model, and the model must copy it into `ingest_source`. This includes `get_full_text`.
For an abstract that is cheap. For a full paper it is tens of thousands of output tokens,
and any slip changes the text the kernel stores as the source.

## Decision (proposed)

**A worker is a script, not a service.** For each job it does four things:

1. **Parse.** It calls the parser server as an MCP client, with no model in between:
   - `get_full_text` for papers;
   - docling-mcp, or markitdown-mcp, for other documents.
2. **Ingest.** It passes the result to the gateway's `ingest_source`, also as an MCP
   client. The document never passes through a model.
3. **Map.** It starts one headless harness session with the pack's skill on the `worker`
   profile (one machine agent, medium trust, kernel tools only). The prompt carries the
   source id and the chunks (ids, heading paths, text): input tokens, not output. The
   model writes claims with `write` and `write_batch` in one extraction run (ADR 0020). It
   closes the run when it has covered the source, and cancels it otherwise.
4. **Record.** It writes the job's outcome to the manifest.

**Job state is a manifest.**
- A JSONL file per batch, as `evals/annotated.py` keeps, records each job's source id, run
  id, status, cost and error.
- Re-running a batch skips jobs that are done.
- A failed job runs again. The script first cancels the job's earlier run if it is still
  ongoing. Cancelling retracts nothing; the new run's close replaces the older reading.
- A queue (a `jobs` schema owned by its own role, or a queue service) comes only when jobs
  must be shared across machines. It would stay outside the `kernel` schema.

**Parsers.** Each parser server runs as it ships, beside the gateway, and only the worker
calls it.
- Papers: GROBID through the papers server.
- Other documents: docling-mcp for layout, tables and OCR. It needs Hugging Face for its
  models, which this environment blocks. markitdown-mcp is the fallback.
- Their Markdown is ingested as `text/markdown`. `metadata.parser` records the converter
  and its version.

**Long calls.** GROBID can take a minute on a long PDF. Where client and server support MCP
Tasks, the parse runs as a task. Until then the script calls with a long timeout.

**Not used.**
- Graphiti and other memory servers: a second write path.
- Sampling: Claude Code does not support it.
- LangExtract: not now. Revisit it if one-shot extraction of a simple schema at volume
  matches agentic runs on the annotated eval.

**Interactive use.** An agent mapping one paper may still call `get_full_text` and ingest
the result itself, at the cost above. Bulk full texts go through the worker.

**The invariants hold.**
- Invariant 1: the worker writes only through the gateway (`ingest_source`, `write`,
  `write_batch`) and holds no database credentials.
- Invariant 4: nothing about jobs enters the log.
- Invariant 5: model calls happen in the harness session, never in the database or inside
  a transaction.
- Invariant 10: fetching PDFs acts on the outside world. That happens in the papers server
  and the worker, outside the kernel.

**The gate.** Workers are built only when the annotated eval meets floors agreed for the
pack, each a lower bound of a 95% interval, not a point estimate:
- verdict accuracy;
- evidence capture;
- rationale precision.

Below those floors, bulk extraction would fill the graph with readings no one should trust
at scale. Until then, extraction stays interactive or per paper.

**Throughput.** One append lock serialises writes. A batch of 50 small writes takes well
under a second, so the model, not the kernel, bounds throughput. Workers run in parallel up
to the model's rate limits. Stale-read rejections between workers touching the same nodes
are expected and retried by the harness, as now.

## Consequences

- No new service, schema or role. The worker is a script on the MCP client the evals
  already use.
- Ingesting costs no model tokens. Mapping costs input tokens for the text and output tokens
  for the claims.
- The script is its own scheduler: jobs are listed by hand (DOIs, files). A scheduler is an
  ops loop and stays out of scope.
- Dropped from the first version: the `jobs` schema, the `wmk_jobs` role, `SKIP LOCKED`
  claims and the sweep for abandoned runs.

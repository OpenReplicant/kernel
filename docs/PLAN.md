# Plan

One milestone at a time. Each has a gate (tests that pass), reported with the commands run and
their output. Ideas that don't serve the current milestone go to `docs/BACKLOG.md`.

The order follows the lifecycle in `docs/DESIGN.md`, but retrieval comes right after storage,
and each later milestone is gated on retrieval getting better or staying as good: capture
without recall is what went wrong before (docs/LESSONS.md).

## M0 — Spike

Throwaway code under `spike/`, findings in `docs/SPIKE_NOTES.md`.

1. `docker compose up`: AgensGraph + pgvector (port the image build from `third_iteration`,
   `db/agensgraph/`) and a Python MCP server (MCP SDK) with one tool reading the graph, over
   streamable HTTP and stdio. Claude Code (`claude -p --mcp-config`) calls it.
2. Embeddings: find a model this environment can run or reach (open question 1); embed a few
   texts; nearest-neighbour through HNSW in a Cypher query. Record speed and dimensions.
3. Full-text: a `tsvector` index on claim and source text; ranked search from Cypher or SQL.
4. Change feed: the outbox trigger (port) wakes an in-process listener in the server; a
   processor writes back with causation and depth.
5. Model for housekeeping: `claude -p` from the server; MCP sampling from Claude Code (does it
   work?).

**Gate:** Claude Code calls a tool on the composed server that runs a hybrid (full-text +
vector + graph) query and returns the expected item; a write wakes a processor that writes
back; the embedding and model options are recorded with evidence.

## M1 — Memory core

Port from `third_iteration` deliberately (`db/core.sql`, `kernel/claims.py`, their S1 tests in
`tests/test_conventions.py` and `tests/test_gate.py`):
things, claims, sources, agents; provenance enforced by the database; supersede, retract,
`why`, `trace`, `as_of`; the outbox and the loop guard. Exposed as MCP tools: `capture`
(a source), `assert` (claims citing a source), `supersede`, `retract`, `get`, `why`, `trace`,
`as_of`. Ontology modules loaded at start-up.

**Gate:** over MCP, a claim carries provenance and time; a superseded claim is still answerable
as of a past time; a claim without provenance is refused; a write loop is stopped by the
guard; `trace` from a computed claim reaches the observations.

## M2 — Retrieval

`recall(query, focus, budget)` → a context pack (ranked, cited, cut to a token budget,
explained); thing lookups; what changed since T. Indexing processor (embeddings, full text).
A **retrieval benchmark** in the repo: a corpus of sources, questions, and the claims that
answer them, including superseded facts (the current value must win) and distractors.

**Gate:** the benchmark runs in the test suite and reports recall@k and context-pack
precision; hybrid recall beats each of full-text alone and vector alone on it; a superseded
value is never returned as current; every item in a pack cites its claim and source.

## M3 — Upkeep

Processors on change and on schedule: Believe (trust policy v1), Contradict, Resolve
(`same_as`, keys and aliases first), Stale. Feedback tools: `used`, `wrong`, `correct`.

**Gate:** two sources disagree and the belief follows the trust policy, with the losing claim
still visible and the conflict listed; a correction by a person supersedes and the belief
follows; two mentions of one thing resolve and recall finds facts stated about either; a
stale belief drops in ranking; the M2 benchmark doesn't get worse, and a benchmark variant
with duplicates and contradictions gets better with the processors on.

## M4 — Claude Code integration

A plugin: MCP config for the server, hooks (session start and prompt submit recall into
context; session end and selected tool results captured as sources), a skill (when to
remember, recall, cite, correct). Captured sessions are encoded by a model encoder (claims
with spans, `inferred`).

**Gate:** in one scripted `claude -p` session a fact is told; in a fresh session the prompt
hook recalls it and the answer cites it; a correction in a third session supersedes it and the
next session uses the new value; every model call the server made is in the graph.

## M5 — Agents for ingestion and reflection

Bulk ingest ("study" a folder or repo as one batch: extract, resolve over the whole batch,
one change event at the end) and reflection (Consolidate episodes, Reflect for gaps and
patterns), each budgeted and recorded.

**Gate:** a small corpus studied in one batch answers the benchmark's questions about it;
consolidation of a week of episodes keeps the benchmark score while shrinking the context
packs; every conclusion traces to its sources.

## Later (order decided after M3)

- Knowledge packs (export and import a subgraph with provenance).
- Integrations for other harnesses' hooks; MCP clients of other servers as encoders.
- A RuleGo port or RuleGo harness calling this server (docs/LESSONS.md: through MCP from a
  Python step, not RuleGo's `ai/mcpClient` as it stood).
- Several entities or users on one server; auth.

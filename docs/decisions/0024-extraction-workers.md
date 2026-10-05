# 0024. Extraction workers: a queue outside the kernel, workers as gateway clients

Date: 2026-10-04 · Status: proposed (design only; nothing here is built until reviewed)

## Context

The research track plans workers for bulk extraction once a live eval shows extraction
quality, and CLAUDE.md asks for an ADR first on how job queues fit invariant 1 (one write
path). The annotated-corpus eval (`make annotated`) now measures extraction against
independent annotations. The question is where jobs and their state live, and who writes.

## Decision (proposed)

**The queue is not kernel data.** Jobs live in their own store: a `jobs` schema in the same
Postgres instance, or any queue. It is owned by a `wmk_jobs` role with no grants on the
`kernel` schema. A job names:
- a source to fetch or a source id already ingested;
- the skill and its version;
- the model;
- the profile.

Its states (queued, claimed, done, failed) and retry counts change freely: they are
operations bookkeeping, not facts about the world.

**Workers are gateway clients.**
- A worker claims a job (`SELECT ... FOR UPDATE SKIP LOCKED` in the jobs schema). It then
  runs a harness (headless Claude Code, or another MCP client) against the gateway on the
  `worker` profile: one machine agent per worker pool, medium trust, kernel tools only.
- Everything the worker learns enters through `ingest_source`, `write` and `write_batch`,
  checked like any other write. The worker holds no database credentials for `kernel`.
- Each job is one extraction run (ADR 0020). It closes the run when it covers the whole
  source and cancels it otherwise. Re-running a job replaces the earlier reading instead of
  adding to it.

**Invariant 1 holds.** The queue never writes to the kernel, and the kernel never reads
the queue. Moving a job to `done` and the job's kernel writes are separate transactions.
A worker that dies between them leaves an ongoing run and a job that is retried. The retry
starts a new run; the abandoned run stays `ongoing` until a sweep cancels it, which
retracts nothing.

**Invariant 5 holds.** Model calls happen in the worker, never in the database or inside a
transaction.

**The gate.** Workers are built only when the annotated eval meets floors agreed for the
pack, with each floor a lower bound of a 95% interval, not a point estimate:
- verdict accuracy;
- evidence capture;
- rationale precision.

Below those floors, bulk extraction would fill the graph with readings no one should
trust at scale. Until then, extraction stays interactive or per paper.

**Throughput.** One append lock serialises writes. A batch of 50 small writes takes well
under a second, so the model, not the kernel, bounds throughput. Workers run in parallel
up to the model's rate limits. Stale-read rejections between workers touching the same
nodes are expected and retried by the harness, as now.

## Consequences

The queue can be swapped (Postgres, Redis, a cloud queue) without touching the kernel.
Operations data never enters the log, so replay ignores it.

Costs:
- a `jobs` schema and role;
- a worker profile;
- a sweep for abandoned runs;
- a scheduler, which is an ops loop and out of scope until approved: until then, jobs are
  enqueued by hand or by a script.

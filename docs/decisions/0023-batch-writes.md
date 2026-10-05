# 0023. write_batch: several claims in one call, each its own kernel.write

Date: 2026-10-04 · Status: accepted

## Context

Mapping one abstract takes a model 15 to 20 tool calls, most of them writes, and each call
re-reads the whole conversation. Cost and latency come from the number of calls, not the
writes. The design keeps one write path: bulk loading is many payloads through
`kernel.write`, never a direct import. Graphiti's history shows what fast paths cost.

## Decision

The gateway gains an eighth tool, `write_batch`: up to 50 writes (claim, ops, unresolved)
and one `read_at_offset`.

- Each write goes through the same `kernel.write` as the `write` tool, in order and in its
  own transaction, with the same rules, stale-read check, resolution cascade and belief.
  The kernel is unchanged, and invariant 1 holds.
- A `$name` ref created by an earlier write in the batch can be used by later writes: in op
  fields that point at things (`from`, `to`, `node`, `about`, `edge_id`, `run`, ...) and in
  `claim.run`. A write that defines the same ref uses its own. The gateway substitutes the
  ids before calling the kernel, so the log only ever sees ids.
- The batch stops at the first rejection. Writes before it stay committed. The problem
  document is the rejection's own, extended with:
  - `batch_index`: which write failed;
  - `written`: the results of the earlier writes;
  - `not_attempted`: how many writes came after it.

  The model fixes that write and sends it again with the rest.
- No write waits on another's result inside the database, and no transaction spans the
  batch: a long batch never holds the append lock between writes.

## Consequences

An extraction run can go in a handful of calls: the run and the paper first, the findings,
then `close_run`. The rejection rate per write is unchanged, but a rejection now also
returns the writes before it, so the model must resend only the rest.

Skills say when to batch: several claims from one source that the model has already read
and looked up. They do not batch across sources, or when a later claim depends on reading
the result of an earlier one.

Partial batches are visible in the log as ordinary entries. There is no rollback across
writes, by design: each claim stands on its own provenance.

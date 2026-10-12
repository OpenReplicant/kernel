# Instructions for Claude Code

Read `README.md`, then `docs/DESIGN.md`, `docs/PLAN.md` and `docs/LESSONS.md` before writing
code. Earlier iterations are on the `first_iteration`, `second_iteration` and
`third_iteration` branches; port from them deliberately (`docs/LESSONS.md` lists what), don't
copy wholesale.

## Working rules

- **One milestone at a time** (`docs/PLAN.md`). Don't start the next until the current gate
  passes, and report the gate result with the commands run and their output.
- **Scope discipline.** This repo is the memory server. Running agents for their own tasks,
  sandboxes, code generation and harness features are a different product: they go into
  `docs/BACKLOG.md` (or that product), not into code here. Other ideas go there too.
- **Retrieval and upkeep are not afterthoughts.** A change to capture or encoding says how
  the thing will be found again; the retrieval benchmark (from M2) must not get worse.
- **Verify, don't assume.** Facts about AgensGraph, pgvector, the MCP SDK, embedding models
  and Claude Code are checked by experiment and recorded in `docs/SPIKE_NOTES.md` before the
  design depends on them.
- **Provenance is not optional.** Every claim records its source and span, method,
  confidence, agent and times. Changes supersede; nothing is edited silently. Never upgrade a
  guess to observed or stated.
- **The server's own model calls are evidence.** Each one is recorded as a source (request,
  reply, model, tokens, cost) and its conclusions cite it.
- **Scripts committed to the repo are reviewed code** and are shown to the user before they
  first run.
- **Secrets from the environment** (`DATABASE_URL`, API keys). Never commit them.
- **Tests are the deliverable.** A milestone is done when its gate passes.

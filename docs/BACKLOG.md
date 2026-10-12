# Backlog

Ideas parked until a milestone needs them.

- **The harness product** (separate repo): agents running their own tasks on top of this
  memory, with a sandbox for generated code, model routing (LiteLLM), agent harnesses
  (`claude -p` with only sandbox tools). `third_iteration` has working pieces: `sandbox/`,
  `kernel/harnesses.py`, `kernel/providers.py`, `docs/SANDBOX.md`, `docs/HARNESSES.md`.
- **RuleGo**: a port of the server, or a RuleGo harness calling it over MCP from a Python
  step. RuleGo's `ai/mcpClient` as built in iteration three never connected on its own
  (`third_iteration` `docs/SPIKE_NOTES.md`, S5).
- Hooks for harnesses other than Claude Code, as they get hook systems.
- Modules and processors described in the graph itself, so the memory can describe and
  rearrange its own upkeep.
- Several users or entities on one server, with auth and per-user views.
- Retention: archiving or summarising old sources; pruning the outbox.
- Write permissions by database role instead of trusting writers to set causation and depth.

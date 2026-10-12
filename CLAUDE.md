# Instructions for Claude Code

Read `README.md`, then `docs/DESIGN.md`, `docs/PLAN.md` and `docs/LESSONS.md` before writing
code. The previous iteration is on the `second_iteration` branch; port from it deliberately,
don't copy wholesale.

## Working rules

- **One milestone at a time** (`docs/PLAN.md`). Don't start the next until the current gate
  passes, and report the gate result with the commands run and their output.
- **Scope discipline.** Build only what the milestone asks for. Other ideas go into
  `docs/BACKLOG.md`, not into code.
- **Verify, don't assume.** Facts about AgensGraph, pgvector and RuleGo are checked by
  experiment and recorded in `docs/SPIKE_NOTES.md` (S0) before the design depends on them.
- **The graph runs nothing; chains do everything.** Knowledge lives in the graph, behaviour in
  RuleGo chains, capabilities in modules of the types in `docs/DESIGN.md`.
- **Provenance is not optional.** Every claim records its source, method, confidence and
  times. Changes supersede; nothing is edited silently. Never upgrade a guess to observed or
  stated.
- **Results land in the graph.** Anything a run produces that matters (outcomes, metrics,
  what a model said) is recorded as evidence, not only in files.
- **Generated code runs only in a sandbox** (no network, no secrets, resource limits). Scripts
  committed to the repo are reviewed code and are shown to the user before they first run.
- **Secrets from the environment** (`DATABASE_URL`, API keys). Never commit them.
- **Tests are the deliverable.** A milestone is done when its gate passes.

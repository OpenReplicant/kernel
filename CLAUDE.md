# World-model kernel + paper compiler — instructions for Claude Code

This repo builds **version 1** of two layers:

1. **The kernel**: a general, domain-free store for an evidence-based world model. Things,
   roles, couplings and assertions, each with provenance, context and two times, and each
   traceable to the recording that supports it (document, observation, transcript, trace).
   Applications talk to it only through its interfaces.
2. **Its first application, the paper compiler**: turns an agent-design paper into a
   decomposed system in the kernel, runs it, checks its mechanism is present, and records the
   results back into the kernel as evidence.

Read these before writing code, in this order:

1. `docs/VISION.md` — the long-term loop and where v1 sits in it
2. `docs/KERNEL.md` — the kernel's constructs, schema and interfaces (the contract)
3. `docs/CONCEPTS.md` — the agent-design vocabulary: primitives, scopes, mechanism tests
4. `docs/V1_BUILD_PLAN.md` — milestones M0–M9, each with an acceptance gate
5. `docs/SCRIPT_CONTRACT.md` — the one interface every step script follows
6. `docs/FILESYSTEM.md` — what lives in git, in the data root, and in Postgres
7. `vocab/*.yaml`, `examples/reflexion/spec.yaml`, `schema/spec.schema.json`

`docs/target/PLATFORM_PLAN.md` is an older, broader design. It is context, not scope.

## Working rules

- **The kernel is domain-free.** Nothing under `kernel/` may import from `apps/`, mention
  papers, slots or agent designs, or hard-code a vocabulary other than `vocab/kernel.yaml`
  (its upper ontology: agents, sources, roles, ports, …). Domain
  knowledge enters only through vocabulary files, registered constraints and registered views.
  A test enforces the import rule.
- **Applications use interfaces, not tables.** Code under `apps/`, `registry/` and the compiler
  skill calls the `kernel` package or `scripts/kernel_*.py`; it never writes SQL against `kb.*`.
- **Assertions are never edited.** Corrections are `supersede`; removals are `retract`.
- **Provenance is sacred.** `stated` and `observed` need evidence spans (the database
  enforces it). Anything else is `inferred`, `computed` or `defaulted`. Never upgrade a guess
  to `stated`. The spec's `repo` label is `stated` with a span in the pinned repo.
- **Scope discipline.** Build only what the current milestone asks for. Other ideas go into
  `docs/BACKLOG.md`, not into code.
- **Milestones in order.** Don't start one until the previous gate passes (M0 may run
  alongside M1–M3; it gates M7). Report the gate result (commands run and their output)
  when you finish each.
- **Verify, don't assume, RuleGo details** (M0). Record findings in `docs/RULEGO_NOTES.md`.
- **Stock RuleGo only.** No custom Go components in v1. Chains wire Python scripts together.
- **Agents are chains, not RuleGo's agent node.** Don't use the `ai/agent` component. An agent
  loop is a chain of slot steps that re-invokes itself until its controller says stop. Keep
  slots as separate steps so a paper's distinctive parts can be swapped and combined.
- **Two kinds of code, treated differently.**
  - *Payloads* (solutions the agent writes, benchmark code, anything a model produces at run
    time) execute only inside the Podman sandbox: no network, no mounted secrets,
    CPU/memory/time limits. Never `exec`/`eval` them on the host.
  - *Step scripts* (including ones the compiler generates) run on the host, so they are code
    under review: committed to the repo and shown to the user before they first run.
- **Secrets from the environment.** `ANTHROPIC_API_KEY`, `DATABASE_URL`. Never commit them.
- **Every model call goes through `scripts/claude_call.py`**, which enforces the per-run budget.
- **Tests are the deliverable.** A milestone is done when its gate passes, not when code exists.

## Stack (v1)

- Python 3.11+, `psycopg` 3, `anthropic`, `pytest`, `pyyaml`, `jsonschema` (`pyproject.toml`)
- PostgreSQL 16, plain (no extensions in v1). Schemas in `db/001_kernel.sql`, `db/002_runtime.sql`
- RuleGo server, pinned version; where it runs (host or container) is decided in M0
- Podman, rootless, for sandboxes and Postgres (`podman compose` or `podman-compose`)
- No MCP in v1; every script is tool-shaped (JSON Schema in and out) so it can be exposed later

## Layout to create

```
compose.yml               postgres (+ rulego if M0 puts it in a container) + sandbox image build
db/                       001_kernel.sql, 002_runtime.sql (exist)
vocab/                    kernel.yaml, agent_design.yaml, infra.yaml (exist)
kernel/                   the kernel library — domain-free
apps/paper_compiler/      first application: constraints, agent-spec view, report generation
pclib/                    script-contract library (stdin/stdout, steps, traces, model calls)
scripts/                  tool-shaped scripts: kernel_*.py, claude_call.py, env_container.py, ...
scripts/schemas/          JSON Schemas for every script's input and output
registry/                 slot implementations, one script each (registered in the kernel)
tools/                    validate_spec.py (exists)
papers/reflexion/         exported views and run artifacts: spec.yaml, SPEC.md, chains, prompts, REPORT.md
bench/                    HumanEval slice + runner
tests/                    kernel/, contract/, mechanism/, smoke/
data/                     $PC_DATA default, git-ignored (see docs/FILESYSTEM.md)
.claude/skills/compile-paper/SKILL.md   (M9)
```

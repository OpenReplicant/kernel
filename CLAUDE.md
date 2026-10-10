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
2. `docs/ARCHITECTURE.md` — layers (kernel, systems, domains, applications) and modules
3. `docs/KERNEL.md` — the kernel's constructs, schema and interfaces (the contract)
4. `docs/CONCEPTS.md` — the agent-design vocabulary: primitives, scopes, mechanism tests
5. `docs/V1_BUILD_PLAN.md` — milestones M0–M9, each with an acceptance gate
6. `docs/SCRIPT_CONTRACT.md` — the one interface every step script follows
7. `docs/FILESYSTEM.md` — what lives in git, in the data root, and in Postgres
8. `vocab/kernel.yaml`, `modules/*/vocab.yaml`, `examples/reflexion/spec.yaml`, `schema/spec.schema.json`

`docs/target/PLATFORM_PLAN.md` is an older, broader design. It is context, not scope.

## Working rules

- **The kernel is domain-free.** Nothing under `kernel/` may import a module, mention
  papers, slots or agent designs, or hard-code a vocabulary other than `vocab/kernel.yaml`
  (its upper ontology: agents, sources, roles, ports, …). Domain
  knowledge enters only through modules: vocabulary files, registered constraints and views.
- **Layers and modules** (docs/ARCHITECTURE.md). A module imports only `kernel`, `pclib` and
  the modules it declares in `module.yaml`; tests enforce it.
- **Modules use interfaces, not tables.** Code under `modules/` and the compiler skill calls
  the `kernel` package or `scripts/kernel_*.py`; it never writes SQL against `kb.*`.
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
- **RuleGo build lives in `rulego/`.** Our server module compiles in upstream components
  as needed (`rulego/server/components.go`, each with its reason); our own Go only with a
  reason a Python step can't serve. Chains wire Python steps together.
- **Agents are chains, not RuleGo's agent node.** Don't use the `ai/agent` component. An agent
  loop is a `while` node over an acyclic iteration chain of slot steps, repeated until its
  controller says stop (recursion through `flow` where a design needs it). Keep slots as
  separate steps so a paper's distinctive parts can be swapped and combined.
- **pclib is the node.** Steps are scripts defining `main(inp)`; chains run them on the
  `x/python` node through stubs that `pclib.chains` generates. Build chains with
  `pclib.chains`, never by hand (docs/RULEGO_NOTES.md).
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
- RuleGo-Server v0.38.0 + `x/python`, built from `rulego/server` into `rulego/Containerfile`
- Podman, rootless, for sandboxes and Postgres (`podman compose` or `podman-compose`)
- No MCP in v1; every script is tool-shaped (JSON Schema in and out) so it can be exposed later
  (RuleGo-Server can serve chains as MCP tools; docs/RULEGO_NOTES.md §7)

## Layout to create

```
compose.yml               postgres + rulego + sandbox image build
rulego/                   the runtime build: Containerfile, config.conf, server/ (Go module), spike/
db/                       001_kernel.sql, 002_runtime.sql (exist)
vocab/                    kernel.yaml: the upper ontology
kernel/                   the kernel library — domain-free (L0)
modules/systems/          L1: components, role instances, boundary, behaviour tests, chain compilation
modules/agent_design/     L2: slot types, scopes, agent-design constraints, slot components (steps/)
modules/evaluation/       L2: models, benchmarks, metrics, results
modules/paper_compiler/   L3: papers, claims, the agent-spec view, reports
pclib/                    step contract + RuleGo node (node.py), chain builder (chains.py), client (rulego.py)
scripts/                  tool-shaped scripts: kernel_*.py, claude_call.py, env_container.py, ...
scripts/schemas/          JSON Schemas for every script's input and output
tools/                    validate_spec.py (exists)
papers/reflexion/         exported views and run artifacts: spec.yaml, SPEC.md, chains, prompts, REPORT.md
bench/                    HumanEval slice + runner
tests/                    kernel/, contract/, mechanism/, smoke/
data/                     $PC_DATA default, git-ignored (see docs/FILESYSTEM.md)
.claude/skills/compile-paper/SKILL.md   (M9)
```

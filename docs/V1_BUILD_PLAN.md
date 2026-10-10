# Version 1 build plan

**Goal:** build the world-modeling kernel with its application interfaces, then build its first
application on top: compiling an agent-design paper into a decomposed, runnable system whose
mechanism is provably present, with results recorded back into the kernel as evidence.

**Version 1 gate:** the kernel passes its own tests with no application code loaded; the
Reflexion system, stored as a closed kernel context and exported as `spec.yaml`, runs as a
RuleGo chain of Python scripts, passes its mechanism tests while a fault-seeded variant fails
them, and its benchmark results sit in the kernel as `observed` assertions with their
conditions and a provenance trail back to the paper.

Work through the milestones in order. Each ends with a gate: run the listed checks and report
their output before moving on.

| Part | Milestones |
|---|---|
| Runtime check | M0 |
| Kernel | M1 foundation · M2 kernel core · M3 vocabularies, validation, views |
| First application | M4 model calls · M5 sandbox · M6 paper as evidence + slots · M7 Reflexion by hand · M8 benchmark → evidence · M9 compiler |

---

## M0 — Spike: confirm the RuleGo assumptions

The design assumes things about RuleGo that haven't been confirmed. Check them in the pinned
version's docs and source, try each one with a toy chain, and write the findings to
`docs/RULEGO_NOTES.md`.

1. **Distribution.** Which artifact to run (RuleGo server image or binary), how chains are
   loaded (folder, API), and how to trigger a chain over HTTP and get its result back
   synchronously.
2. **Command execution node.** Its type name and options: command, arguments, environment,
   working directory, timeout, and how stdin/stdout map to message data and metadata.
3. **Agent loops as self-invoking chains.** Agents are built as chains, not with RuleGo's
   `ai/agent` node. Test how a chain can run again until a controller step says stop, in this
   order of preference:
   - **Re-enqueue:** the last step sends a new message to the same chain's entry point, so
     each iteration is a fresh, shallow execution (no growing call stack).
   - **Sub-chain call in a loop:** a parent chain invokes the iteration chain repeatedly.
   - **Back-edge or recursion** inside one chain, if supported; record depth and time limits.
   Also check how a sub-chain is invoked from another chain and how results come back.
4. **Rule-engine AOP.** Check whether the core engine's aspects (not the agent node's) apply
   to every node in any chain, which hook points exist, and whether they can be configured
   without writing Go. Record findings only.
5. **Conditional routing** on a field of the message (e.g. `passed == true`).
6. **Where RuleGo runs relative to Podman.** Scripts must start sandbox containers. Prefer
   running RuleGo and the scripts directly on the host as a normal user with rootless Podman
   (Postgres stays in Compose). If RuleGo runs in a container instead, it needs the user's
   rootless Podman socket; record the choice and why. Never use a rootful socket.

**Fallbacks, if a point fails:**
- No usable exec node → chains call a small Python HTTP service with a stock REST node, same
  JSON contract.
- No usable self-invocation → a driver script runs iterations and calls the iteration chain
  per step over RuleGo's HTTP API. The slot steps stay identical.

**If M0 goes badly** (several fallbacks needed, or RuleGo behaves unpredictably), stop and
report. The kernel and the spec design don't depend on RuleGo.

**Gate:** a toy chain runs two Python scripts in sequence, passes JSON between them, re-invokes
itself three times with a counter, branches on a field, calls a sub-chain, and returns the
final JSON to an HTTP caller. `docs/RULEGO_NOTES.md` records every option used.

## M1 — Foundation

- `compose.yml` for rootless Podman: Postgres 16 applying `db/001_kernel.sql` then
  `db/002_runtime.sql`; RuleGo if M0 chose a container; a build for the sandbox image.
- The data root per `docs/FILESYSTEM.md`.
- `pclib`: the script contract (`SCRIPT_CONTRACT.md`): stdin/stdout, `run.step` idempotency,
  `trace()`, DB connection, exit codes.

**Gate (pytest, `tests/contract/`):** a sample script returns its stored output on a second
invocation without re-executing; stdout carries only JSON; `trace()` rows are readable by run,
task and episode; a non-zero exit leaves no `run.step` row.

## M2 — Kernel core

The `kernel` Python package implementing the interfaces in `docs/KERNEL.md` sections
"Sources and spans", "Nodes and contexts", "Assertions" and "Queries". No vocabulary
validation yet beyond the kernel vocabulary itself.

- `put_source` stores files by hash under `$PC_DATA/sources/` and is idempotent.
- `assert_` writes the assertion, its arguments and evidence in **one transaction**; the
  database trigger already rejects `stated`/`repo` without evidence, and the library should
  raise a clear error before reaching it.
- Corrections use `supersede`; nothing updates an assertion except `status` and `retracted_at`.
- `query` supports `context` with sub-contexts, `status`, `valid_at` and `known_at`.
- `why` returns the full provenance tree.
- Each operation is also a tool-shaped script: `scripts/kernel_put_source.py`,
  `kernel_assert.py`, `kernel_query.py`, `kernel_why.py` (JSON Schema for input and output
  in `scripts/schemas/`).

**Layering rule, enforced by a test:** nothing under `kernel/` imports from `apps/`.

**Gate (pytest, `tests/kernel/`), using a toy domain unrelated to agents** (for example a
thermostat loop: sensor, controller, heater, coupled through ports):
- store a source and spans; assert facts with each method; `stated` without spans is rejected
- supersede a fact; `query(known_at=<before>)` returns the old value, a current query the new one
- two contradicting assertions coexist in separate perspective contexts and `query` filters by context
- `why` on an assertion returns its spans, source path, agent and method
- bindings and couplings round-trip: `bindings(ctx)` and `couplings(ctx)` return what was asserted

## M3 — Vocabularies, validation, views

- `load_vocabulary` for `vocab/kernel.yaml`, `vocab/agent_design.yaml`, `vocab/infra.yaml`:
  types, roles with ports and cardinality, predicates with domain, range, `max`, `args`, enums.
  Stores each file in `kb.vocabulary` by hash.
- `assert_` now checks predicates exist and subject/object types match domain and range.
- `validate(context)`: the declarative constraints (`required`, `max`, enums,
  `cardinality_by_role`) in the kernel; `register_constraint` for Python checks.
- `promote(staging, target)` refuses unless `validate(target-with-staging)` passes.
- `register_view` / `export` / `import_` in the kernel.
- **First application code**, in `apps/paper_compiler/`:
  - registers the Python constraints named in `agent_design.yaml`
    (`mcp_only_for_model_invoked`, `couplings_reference_declared_ports`)
  - registers the `agent-spec` view: `import_` turns a `spec.yaml` into a closed `system`
    context plus a `perspective` context for the paper's claims; `export` does the reverse.
    Provenance maps to `method`; `source_ref` strings become spans once M6 stores the PDF
    (until then, `stated` fields import as `staged` assertions awaiting evidence).

**Gate:**
- `tests/kernel/` still passes with `apps/` not importable (run it with `apps/` removed from the path)
- importing `examples/reflexion/spec.yaml` then exporting it yields a document equal to the
  original (field by field, ignoring key order) that passes `tools/validate_spec.py`
- `validate` catches the same errors as `tools/validate_spec.py` on a deliberately broken spec
  (internal slot bound as an MCP tool; wiring to an undeclared slot; two policies)

## M4 — Model calls with a budget

- `scripts/claude_call.py` + `pclib.claude()` using the Anthropic Messages API.
- Model ids come from the system context (`in:uses_model`), with env fallbacks
  `PC_ACTOR_MODEL`, `PC_REFLECTOR_MODEL`. Default to a small, cheap model for the actor and
  reflector (e.g. `claude-haiku-5-5`) so HumanEval leaves headroom; that choice is a
  `defaulted` assertion.
- Prompt caching for repeated prefixes. Every `llm.request` trace event carries the calling
  slot's id and the full prompt.

**Gate:** a run with a tiny budget makes calls until the budget is hit, then refuses with a
clear error; trace events and `spent_usd` are correct.

## M5 — Sandbox

- `scripts/env_container.py`: run a Python file plus tests in a fresh rootless Podman container
  per episode, via the `podman` CLI. `--network=none`, mount only the episode's `work/` dir,
  CPU/memory/pids limits, read-only root filesystem, wall-clock timeout, non-root user inside.
  Returns stdout, stderr, exit code, timed_out. Engine command configurable
  (`PC_CONTAINER_CMD`, default `podman`). Emits `tool.call` / `tool.result`.

**Gate:** a passing solution passes, a failing one fails, an infinite loop times out, and code
attempting network access fails.

## M6 — The paper as evidence, and the Reflexion slots

**Ingest the paper.** Store the Reflexion PDF and the pinned repo snapshot as sources. For
every `stated` field in `examples/reflexion/spec.yaml`, find the supporting passage and add it
as a span; for every `repo` field, a span in the pinned repo (path + line range). Then promote
the staged assertions. The example spec was written from memory, so where no passage
supports a `stated` field, supersede it with an `inferred` assertion instead, and list every
such change in `papers/reflexion/SPEC_CHANGES.md`. Pin the repo commit and confirm its license
before copying anything.

**Implement the slots** as scripts in `registry/`, and register each one in the kernel as an
`ad:Component` with `ad:implements`, `ad:implementation_kind` and `ad:entrypoint` assertions
(no separate registry table). Slot resolution is a kernel query.

| Component id | Implements | Does |
|---|---|---|
| `policy/codegen` | Policy | Generates a solution from the problem plus injected context |
| `eval/gen_tests` | Evaluator (output) | Generates unit tests from the problem statement once per task |
| `eval/run_tests` | Evaluator (trajectory) | Runs the solution against generated tests in the sandbox; emits `slot.emit` `pass`/`fail` |
| `reflect/verbal` | Reflector | Writes a short verbal reflection on the failure; emits `slot.emit` `insight` |
| `memory/sliding_window` | Memory (episodic) | Keeps the last `max_items` reflections per task in `run.memory`; emits `memory.write`/`memory.read`; emits `written` only after commit |
| `context/prepend_memory` | ContextBuilder | Prepends retrieved reflections under a header to the actor prompt |
| `control/retry` | Controller | Retries up to `max_trials` on fail; emits `controller.decision` |

Prompts go in `papers/reflexion/prompts/` with a header citing their source span. Model calls
go through `pclib.claude()`, never through the paper repo's own clients.

**Gate:**
- `why` on any `stated` assertion in the Reflexion system context returns a span in the PDF
  with page and character range; every `repo` assertion returns a repo path and line range
- `SPEC_CHANGES.md` lists each field's verification result
- each slot script has a contract test with fixtures (mock `pclib.claude`)
- a kernel query finds a component for every slot in the Reflexion system

## M7 — Reflexion by hand, plus mechanism tests

- Export the Reflexion system as `papers/reflexion/spec.yaml` and render `SPEC.md` from
  `templates/SPEC.md`. These files are views; the kernel context is the source of truth.
- `papers/reflexion/chains/`: the agent loop as a self-invoking chain wired from the
  context's couplings, plus a **baseline toggle** (reflector and memory off, retry only).
  Run strictly in sequence: the retry decision waits until the reflection is stored.
- `tests/mechanism/`: a generic evaluator for mechanism tests read from the kernel
  (`ad:test_body`), evaluated over `run.trace`. Semantics: `expect` events must occur
  **after** the `given` event and inside the `within` window; with `negate`, none may.
- **Fault seeding:** a variant that writes reflections but never injects them.
- Each run creates a `run` context in the kernel whose `conditions` record models, benchmark
  version, budget and toggles. Mechanism-test outcomes become `observed` assertions
  (`ad:mechanism_test_passed`) in that context.

**Gate:** on 5 HumanEval problems, Reflexion passes all mechanism tests; the fault-seeded
variant fails `reflection_reaches_next_trial`; the baseline produces no reflector events; all
three outcomes are queryable in the kernel.

## M8 — Benchmark results as evidence

- `bench/`: vendor HumanEval as a source (check its license), pick a fixed random slice of 40
  problems with a recorded seed. Score final solutions against HumanEval's **hidden** tests in
  the sandbox, separate from the self-generated tests used inside the loop.
- **Ask the user for a dollar budget before the first full run.** First run 3 problems per arm
  and extrapolate the full cost from actual spend.
- Run baseline and Reflexion with the same models, `max_trials` and budget.
- Write results as `observed` assertions (`ad:metric`) in each run's context. The paper's own
  reported numbers are `stated` assertions in the paper's perspective context, with spans.
- `papers/reflexion/REPORT.md` is generated **from kernel queries**: pass@1 per arm, trials,
  cost, mechanism-test outcomes, the paper's reported numbers beside ours with their
  differing conditions, and the system's `inferred`/`defaulted` assertions as candidate
  explanations for any gap.

40 problems can't establish statistical significance; report the numbers as directional
evidence. If the baseline already passes nearly everything, note it and add "harder
benchmark" to the backlog rather than switching mid-milestone.

**Gate:** REPORT.md regenerates from the kernel alone; one query answers "for Reflexion vs
baseline on HumanEval, under which conditions, what was observed, and on what evidence";
spend is under budget.

## M9 — The compiler

A Claude Code skill at `.claude/skills/compile-paper/SKILL.md` that, given an arXiv id or PDF:

1. Stores the PDF (and pinned repo, if any) as sources.
2. Writes the paper's design as assertions into a **staging** context, each with spans for
   `stated`/`repo` facts, using the agent-design vocabulary. Claims and mechanism tests go in
   the paper's perspective context.
3. Resolves each slot by querying the kernel for components that implement it; only
   unmatched slots get new scripts (shown to the user before they first run).
4. Stops for human review: lists every `inferred` and `defaulted` assertion with its reasoning.
5. On approval, `promote`s staging into a closed system context (which runs `validate`).
6. Exports `spec.yaml` and `SPEC.md`, generates chains from couplings and mechanism tests from
   the kernel, runs contract and mechanism tests.

The skill uses only the kernel's interfaces (library or `scripts/kernel_*.py`), never SQL.

**Keep the gate honest.** The compiler must not see the answers. Run the gate in a fresh
session against a copy of the repo **and a fresh database** with the hand-built Reflexion
context, `examples/reflexion/` and `papers/reflexion/` removed. Registry components may stay.

**Gate, two parts:**
1. Compile Reflexion from its arXiv id. Diff its system context against the hand-built one
   (roles, bindings, couplings, params, tests); the compiled chain passes the same mechanism
   tests. Report the diff.
2. Compile **Self-Refine** (arXiv 2303.17651), a paper nobody hand-built. Report which slots
   resolved to existing components versus new code, and whether its mechanism tests pass.

---

## Not in v1

Everything in `docs/BACKLOG.md`. If a milestone seems to need one of those items, stop and ask
rather than building it.

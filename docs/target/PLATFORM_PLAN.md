> **Older, broader design; not version 1 scope.** Version 1 is defined in `docs/V1_BUILD_PLAN.md`
> and the kernel in `docs/KERNEL.md`, which supersede this file where they differ.
> Later decisions supersede parts of this file: RuleGo is the only runtime, steps start as
> Python scripts, and the stores, control plane and gateway described here wait in `docs/BACKLOG.md`.

# Paper Compiler Platform Plan

Version 0.1 · 2026-10-09

## 1. What this is

A system that ingests an agent-design paper and emits a runnable, testable
implementation as a folder: `SPEC.md`, `spec.yaml`, RuleGo rule chains,
component scripts, prompts, asset manifests, benchmark environment, tests and a
reproduction report. Compiled papers become toggles that can be composed and
ablated against each other.

**Runtime decision:** RuleGo with `rulego-components-ai` is the only runtime.
The Agent SDK is dropped. Its tuned harness would confound reproductions, since
most papers ran on thin loops, and RuleGo's ordered AOP aspects already cover
every interception point the SDK offers plus message-level ones it lacks.

## 2. Layers

```
┌──────────────────────────────────────────────────────────────┐
│ Lab: toggle board, composition checks, ablation grid, reports │
├──────────────────────────────────────────────────────────────┤
│ Paper compiler: skill + agent  (paper ──▶ folder)             │
├──────────────────────────────────────────────────────────────┤
│ Platform                                                      │
│   Slot registry    primitive interfaces + reusable components │
│   Asset resolver   asset kinds ──▶ running providers          │
│   Control plane    environments: provision/exec/snapshot/...  │
│   Service gateway  external APIs: secrets, limits, replay     │
│   RuleGo runtime   chains, aspects, MCP endpoint, exec nodes  │
└──────────────────────────────────────────────────────────────┘
```

## 3. How a primitive gets implemented

Each slot binding in a spec resolves in this order and stops at the first hit:

1. **Registry.** A component already in the slot registry that implements the
   interface. Params and prompts are supplied by the spec.
2. **Paper code.** The paper's published repo, pinned to a commit, wrapped by
   an adapter so it meets the slot interface.
3. **Generated.** New code written by the compiler from the paper text.

The implementation **kind** is chosen separately from the source:

| Kind | Use for | Notes |
|---|---|---|
| `aspect` | Context builder, observation processor, policy wrap | Maps to RuleGo AOP aspects; `order` in the spec becomes aspect Order |
| `rulego_node` | Native Go components | Fastest; used for registry-grade primitives |
| `exec_node` | Python or CLI scripts | Default for paper code and first-pass generated code |
| `sub_chain` | Multi-step primitives (e.g. reflect = summarise + store) | Composable, hot-reloadable |
| `mcp_tool` | Anything the **model** calls as a tool | Action space, memory operations exposed to the model |

### The MCP rule

MCP is a tool protocol: the model decides when to call it. That is correct for
action space and model-visible memory operations (MemGPT's paging calls,
Voyager's skill lookup if the agent invokes it) and wrong for internal
primitives. An evaluator exposed as MCP would let the model choose whether to
grade itself, which changes the paper. The schema enforces this by requiring an
`mcp_server` asset only for `mcp_tool` bindings; review flags any evaluator,
reflector, controller, search or aggregator bound as `mcp_tool`.

## 4. MCP sourcing

When a slot resolves to `mcp_tool`, its server is sourced in this order:

1. **Registry.** An MCP server the platform already runs (from earlier papers
   or the public MCP registry). Reuse only if its tool semantics match the
   paper; a "search" tool with different ranking is a fidelity gap, not a match.
2. **Paper code.** The paper's repo, pinned to a commit with a compatible
   license recorded. Paper code is rarely an MCP server already, so the
   compiler writes a thin adapter that calls it through an `exec_node` or
   `sub_chain`, then exposes that chain through RuleGo's MCP server endpoint.
   This keeps the paper's exact logic while giving a uniform interface.
3. **RuleGo-generated.** New rule chains or components implement each tool,
   and RuleGo's MCP endpoint exposes them. Tool names, descriptions and input
   schemas come from the paper where stated; otherwise they are `[inferred]`.

**Every sourced server must:**

- Declare its tools in `spec.yaml` (`assets[].mcp.tools`) with the backing
  chain or component for each.
- Pass contract tests: tool list matches the declaration, each tool accepts its
  input schema, and errors come back as tool errors rather than crashes.
- Run in-process by default. `stdio` or `http` only when the paper code needs
  its own runtime (e.g. a Python env with heavy deps), in which case it runs
  inside a control-plane environment (section 5).
- Be promoted to the registry after passing, unless `promote_to_registry:
  false`. Promotion records the source paper so later reuse is traceable.

**License gate.** If the paper repo has no license or an incompatible one, the
compiler falls back to `rulego_generated` and records a fidelity gap. It never
copies unlicensed code into the folder.

## 5. Environment control plane

Many papers need somewhere to act: a code sandbox (CodeAct, SWE-agent), a
browser (WebArena-style tasks), a game or simulator (Voyager), or a benchmark
harness. Search papers additionally need to **branch** that environment.

### 5.1 Interface

One interface, implemented by drivers, exposed to rule chains as custom RuleGo
components under `env/*`:

| Operation | Purpose |
|---|---|
| `provision(spec) → handle` | Start an instance from an `environment` asset |
| `exec(handle, cmd, timeout) → result` | Run a command; returns stdout, stderr, exit code |
| `files(handle, op, path, data?)` | Read, write, list |
| `reset(handle)` | Return to initial state via `reset_command` if cheaper than reprovisioning |
| `snapshot(handle) → snap_id` | Capture state for branching |
| `restore(snap_id) → handle` | New instance from a snapshot (a branch) |
| `health(handle)` | Readiness check |
| `teardown(handle)` | Release resources |

Every operation emits a trace event (`env.snapshot`, `env.restore`, …) so
behavior tests can assert on environment handling, e.g. "each ToT branch
restores from its parent's snapshot."

### 5.2 Drivers

| Driver | When | Build order |
|---|---|---|
| `docker` | Local runs, CI, single host | **First.** Covers most papers |
| `kubernetes` | Large ablation grids, many parallel branches | Second, behind the same interface |
| `external` | Something already running that we only attach to (a hosted benchmark, a long-lived simulator) | As needed; supports `exec`/`health` only |

### 5.3 Lifecycles

| Lifecycle | Meaning | Typical papers |
|---|---|---|
| `per_run` | One instance for the whole benchmark run | Read-only tools, static corpora |
| `per_task` | Fresh per task, shared across retries | Reflexion (retries see the same task) |
| `per_episode` | Fresh or `reset` per attempt | Most sandboxed coding tasks |
| `per_branch` | Snapshot/restore at each branch point | ToT, LATS, RAP with real envs |

### 5.4 Snapshotting

Branching is the expensive part, so the spec names a method:

| Method | Cost | Fidelity | Use when |
|---|---|---|---|
| `app_level` | Lowest | Exact if the app supports it | Simulators with save/load (e.g. game saves) |
| `volume_copy` | Low | Filesystem only, processes restart | Code sandboxes where state is files |
| `image_commit` | Medium | Filesystem only | Docker driver fallback |
| `criu` | High | Full process memory | Stateful processes that cannot be replayed |

The compiler picks the cheapest method that preserves the state the paper's
mechanism depends on, and records anything weaker as a fidelity gap.

### 5.5 Isolation and budgets

- Network defaults to `none`; `allowlist` must be declared per environment.
- Secrets never enter images; they are injected at `provision` from the
  secret store by reference.
- Each environment declares CPU, memory, GPU and a wall-clock timeout. The lab
  enforces a global budget across a grid so a branching paper cannot starve
  the rest of the run.

## 6. External services

Some papers depend on hosted APIs: web search, retrieval services, code
execution APIs, proprietary judges. These are `external_service` assets routed
through a **service gateway** component that handles:

- **Auth** via `secret://` references only.
- **Rate limits and cost** declared in the spec and enforced per run.
- **Determinism mode:**
  - `live`: real calls every time.
  - `record_replay`: first run records responses keyed by request; later runs
    replay. Makes reproductions stable across days and cuts cost in ablation
    grids. Default for search APIs.
  - `mock`: fixtures only, for smoke tests and CI.

A behavior test or benchmark run reports which mode it used, since `live`
results are not strictly comparable across dates.

## 7. Asset resolver

Specs name asset **kinds** with requirements. The resolver maps them to
providers and emits `assets/compose.yaml` (or k8s manifests):

| Kind | Default provider | Alternatives |
|---|---|---|
| `vector_store` | pgvector | Qdrant, LanceDB |
| `kv_store` | Redis | SQLite |
| `relational` | Postgres | SQLite |
| `graph` | Postgres + AGE | Neo4j |
| `blob` / `code_store` | Local volume (+ git for code) | S3-compatible |
| `model_endpoint` | Native Claude Messages API component | OpenAI-compatible endpoint |
| `eval_harness` | Container from `bench/env` | `external` driver |

Providers sharing a kind across toggles share an instance by default, with a
namespace per toggle. That is what lets Reflexion's episodic memory and
ExpeL's insight store coexist when both toggles are on.

## 8. Compiler pipeline

```
paper ─▶ 1 extract ─▶ 2 resolve ─▶ 3 source ─▶ 4 generate ─▶ 5 test ─▶ 6 reproduce ─▶ 7 promote
```

1. **Extract.** Read PDF, appendix and repo. Fill `spec.yaml` with provenance
   on every field. Validate against `schema/spec.schema.json`.
2. **Resolve.** Match each slot to the registry; match each asset kind to a
   provider. List what remains unmatched.
3. **Source.** For unmatched slots and MCP servers, apply the order in
   sections 3 and 4, including the license gate. Pin every repo to a commit.
4. **Generate.** Rule chains, adapters, new components, prompts, asset
   manifests, bench env, tests. Render `SPEC.md` from `spec.yaml`.
5. **Test.** Contract → behavior → smoke. Behavior tests must pass before any
   benchmark run; a failure means the mechanism isn't there, whatever the score.
6. **Reproduce.** Run the benchmark (or declared subset) with baselines. Write
   `REPORT.md`: target vs achieved, determinism modes used, and the
   `inferred`/`defaulted` fields most likely to explain any gap.
7. **Promote.** New components and MCP servers that passed contract tests go
   into the registry with a back-reference to this paper.

**Human checkpoints:** after step 1 (review the spec, especially inferred
fields and any internal primitive bound as `mcp_tool`) and after step 6
(accept or reject the reproduction). Everything else runs unattended.

## 9. Build order

1. Trajectory schema, trace event schema, slot interfaces
2. Native Claude model component for RuleGo
3. Docker driver for the control plane + `env/*` components
4. Registry components: step evaluator, reflector, episodic memory, retry
   controller, aggregator
5. Hand-compile **Reflexion** into the folder format to settle templates
6. Write the compiler skill + agent from that worked example
7. Service gateway with `record_replay`
8. Snapshot/restore + fork component; compile **Tree of Thoughts**
9. Compile **Voyager** (exercises `app_level` snapshots, procedural memory,
   lifetime scope, external environment)
10. Kubernetes driver and the ablation lab

## 10. Open questions

- **Trajectory storage.** One shared trace store across toggles, or per-toggle
  with a merge view? Shared is simpler for cross-paper comparison.
- **Prompt reuse across models.** Papers' prompts were tuned for older models.
  Keep them verbatim for fidelity and add an optional "modernized" variant as
  a separate toggle parameter, never a silent replacement.
- **Composite toggles.** When two papers both `wrap` the policy, is the order
  part of the toggle set definition, or derived? Proposal: explicit in the lab
  config, with the composition check refusing ambiguous sets.
- **Verifying `exec`/MCP endpoint behaviour** in the pinned RuleGo version
  before step 3, since the plan leans on both.

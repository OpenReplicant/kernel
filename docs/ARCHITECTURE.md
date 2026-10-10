# Architecture

One substrate, many uses: a research assistant that turns evidence into a world model, an
agent factory that compiles designs into runnable systems, a software factory that does the
same from a user's spec or session. Each use is a stack of modules on the same parts. It runs
on its own or alongside an agent harness, and any pipeline in it can be replaced by a custom
chain.

## Knowing and doing

The system has two halves that meet at three joints.

- **The kernel knows.** It has two parts:
  - *The record*: an append-only log of sources, spans and assertions with provenance and two
    times, the upper ontology, and the machinery for vocabularies, constraints, views and
    modules. The database enforces its invariants.
  - *Belief*: what is currently held true, computed from the record under a trust policy
    (`docs/KERNEL.md`, Belief). Today that is the `status` lifecycle; a `beliefs()` interface
    comes when more than one source of trust has to be weighed.
- **The runtime does.** RuleGo-Server (built from `rulego/`) runs chains of steps: pclib
  Python steps, model calls, agent-harness sessions, MCP tools, the sandbox. It is a peer of
  the kernel, not part of it. The record has to stay trustworthy whatever the actors do, so
  nothing in the kernel runs anything.

| Joint | Direction | What crosses |
|---|---|---|
| **Describe** | kernel → runtime | A system described in the kernel (roles, components, ports, couplings) compiles to chains (M7). Chains that build systems can be described the same way, so the platform can describe, and later improve, itself. |
| **Read** | kernel → runtime | Steps read facts and beliefs through the kernel's interfaces, including which model, endpoint and parameters a step uses. |
| **Record** | runtime → kernel | Every run is a source: its trace or transcript is the content, the run is a `session` context, and what it observed becomes `observed` assertions with spans into that recording. |

Doing produces evidence for knowing, and knowing decides what to do next.

## Where the kernel stops

The kernel holds what must be true in every domain: identity of things, assertions,
provenance, time, contexts, the vocabulary, constraint and view machinery, and belief
derivation. Anything that *produces* claims (ingesters, models, people) or *acts* (chains,
harnesses, the sandbox) lives outside it.

Applications reach it through four interfaces:

| Interface | What an application supplies | Kernel calls |
|---|---|---|
| **Teach** | Vocabularies, constraints, views | `load_vocabulary`, `register_constraint`, `register_view`, `load_module` |
| **Ingest** | An ingester: a source in, claims with spans out (papers, sensor logs, transcripts, media) | `put_source`, `add_span`, `assert_`, `promote` |
| **Believe** | Queries over the record | `query`, `why`, `bindings`, `couplings`; `beliefs` later |
| **Act** | Chains that run a described system and record the run | the runtime, then Ingest for the run's recording |

An application is a bundle of these. The paper compiler is a vocabulary (Teach), a paper
ingester (Ingest), the `agent-spec` view (Teach), and reproduction chains whose results are
recorded as evidence (Act).

## Layers

| Layer | What it is | Knows about domains? | Lives in |
|---|---|---|---|
| **L0 kernel** | The record and belief: sources, spans, assertions with provenance and two times, the upper ontology (knowing + structure), vocabulary, constraint, view and module machinery | no | `kernel/`, `vocab/kernel.yaml`, `db/` |
| **L1 systems** | The vocabulary for what runs: components implementing roles, parameters, a boundary (entry and exit ports), behaviour tests over traces, sessions. Compiles a closed system context to chains (M7) | no | `modules/systems/` |
| **L2 domain modules** | Vocabularies, constraints and components for one domain | yes | `modules/agent_design/`, `modules/evaluation/` |
| **L3 applications** | Pipelines that use domains for a purpose: ingest sources, compile, run, report | yes | `modules/paper_compiler/` |

The runtime (`rulego/`, `pclib/`) sits beside these layers: modules ship steps and chains for
it, and it reaches the kernel only through the joints above.

Rules: a layer depends only on layers below it; a module imports only `kernel`, `pclib` and
the modules it declares. The kernel never imports a module, and its tests pass with none
loaded.

**System primitives** are L0's (thing, role, port, coupling, context) plus L1's (component,
implementation, role instance, parameter, boundary, behaviour test, session). Domain
primitives are expressed in those terms: an agent-design *slot* is a role type, a slot in a
paper's design is a role instance (`sys:instance_of ad:Reflector`), its implementation a
component that `k:plays` it. Because chain compilation reads only L0/L1 facts, any closed
system context compiles, whether it describes an agent, a thermostat or an ETL pipeline.

## Modules

```
modules/<name>/
  module.yaml     name, version, layer, depends, vocab files
  vocab.yaml      types, roles, predicates, constraints, under the module's own prefix
  __init__.py     register(kernel): Python constraints and views
  steps/          components implementing roles (each also a sys:Component in the kernel)
  chains/         chains the module ships, built with pclib.chains
```

`Kernel.load_module(name)` loads declared dependencies first, then the vocabulary files, then
calls `register(kernel)`. Constraints and views are registered per process; vocabularies are
recorded in `kb.vocabulary`.

| Module | Layer | Prefix | Holds |
|---|---|---|---|
| `systems` | L1 | `sys:` | Component, implements, instance_of, param, order, boundary, assets and environments, behaviour tests and outcomes; constraints that couplings use declared ports in the right direction |
| `agent_design` | L2 | `ad:` | Slot role types (groups, cardinality, ports, internal flag), scopes, subtype, mode, model role, prompts; constraints: exclusive slots, internal slots never model-invoked tools |
| `evaluation` | L2 | `ev:` | Models (and from M4 their endpoints, parameters and prices), harnesses, benchmarks, metrics, settings, acceptance, reported and observed results |
| `paper_compiler` | L3 | `pc:` | Papers, claims, bibliographic facts, fidelity, compatibility; the `agent-spec` view (`spec.yaml`), reports, the compile skill |

Future modules slot in the same way: a `software` domain (requirements, interfaces, tests) for
the software factory, `sessions` for evidence from chats and embodied runs.

## Runtime

RuleGo-Server (built from `rulego/`) runs chains of pclib steps (`x/python` nodes). A system
context compiles to an iteration chain plus a `while` loop and sub-chains; each run is a
session context whose trace file is the evidence for what was observed.

**Model and harness access** (M4, M5). Two kinds of step reach a model, each a sub-chain with
a fixed interface so its implementation can be swapped:

- `model_call`: budget check → the call (RuleGo's `ai/llm` node, or a pclib step where
  `ai/llm` falls short) → usage recorded. Model, endpoint and parameters are read from the
  kernel, so a different model is a different assertion, not different code.
- `harness_session`: an agent harness (Codex, Claude Code, …) run headless or through its MCP
  server in a sandbox, with its own model, endpoint and parameters, given a task and a
  workspace. Its transcript is stored as a source.

Budget lives in these sub-chains. Tracing that every node needs (OpenTelemetry) belongs in an
aspect compiled into `rulego/server`; until then pclib steps trace themselves.

**MCP at the edges, not in the middle.** RuleGo-Server can expose chosen chains as an MCP
server (groups of tools per endpoint), and its `ai/mcpClient` node runs any remote MCP tool as
a chain step. Both are transport. The agent-design rule is about who decides: an internal slot
(evaluator, reflector, controller, …) may be *implemented* by a remote MCP tool the chain calls,
but must never be a tool the model under test can choose to call. Serving the platform's own
chains to a harness over MCP is how it runs *alongside* one (backlog).

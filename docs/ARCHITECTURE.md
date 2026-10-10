# Architecture

One substrate, many uses: a research assistant that turns evidence into a world model, an
agent factory that compiles designs into runnable systems, a software factory that does the
same from a user's spec or session. Each use is a module stack on the same layers.

## Layers

| Layer | What it is | Knows about domains? | Lives in |
|---|---|---|---|
| **L0 kernel** | The evidence-based world model: sources, spans, assertions with provenance and two times, the upper ontology (knowing + structure), and the machinery to load vocabularies, register constraints and views, and load modules | no | `kernel/`, `vocab/kernel.yaml`, `db/` |
| **L1 systems** | How a described system runs: components implementing roles, parameters, a boundary (entry and exit ports), behaviour tests over traces, sessions whose recordings become `observed` evidence. Compiles a closed system context to RuleGo chains (M7) | no | `modules/systems/`, `pclib/`, `rulego/` |
| **L2 domain modules** | Vocabularies, constraints and components for one domain | yes | `modules/agent_design/`, `modules/evaluation/` |
| **L3 applications** | Pipelines that use domains for a purpose: ingest sources, compile, run, report | yes | `modules/paper_compiler/` |

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
| `evaluation` | L2 | `ev:` | Models, benchmarks, metrics, settings, acceptance, reported and observed results |
| `paper_compiler` | L3 | `pc:` | Papers, claims, bibliographic facts, fidelity, compatibility; the `agent-spec` view (`spec.yaml`), reports, the compile skill |

Future modules slot in the same way: a `software` domain (requirements, interfaces, tests) for
the software factory, `sessions` for evidence from chats and embodied runs.

## Runtime

RuleGo-Server (built from `rulego/`) runs chains of pclib steps (`x/python` nodes). A system
context compiles to an iteration chain plus a `while` loop and sub-chains; each run is a
session context whose trace file is the evidence for what was observed.

**MCP at the edges, not in the middle.** RuleGo-Server can expose chosen chains as an MCP
server (groups of tools per endpoint), and its `ai/mcpClient` node runs any remote MCP tool as
a chain step. Both are transport. The agent-design rule is about who decides: an internal slot
(evaluator, reflector, controller, …) may be *implemented* by a remote MCP tool the chain calls,
but must never be a tool the model under test can choose to call. Neither is used in v1.

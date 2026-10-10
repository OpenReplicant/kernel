# Concepts

The agent-design vocabulary used by the first application. Formally it lives in
`vocab/agent_design.yaml` and `vocab/infra.yaml`; the kernel itself is described in
`docs/KERNEL.md`. A **slot** below is a kernel *role*; a component filling it is a *thing*
bound to that role with `k:plays` inside a system context.

## Primitives (slot types)

An agent-design paper is described by which **slots** it fills and how they are wired.

| Group | Slot | What it does |
|---|---|---|
| Decision | `context_builder` | Assembles what the model sees each step (pipeline) |
| | `policy` | Produces the next thought/action (exclusive) |
| | `planner` | Produces and revises plans (exclusive) |
| | `world_model` | Predicts next state without acting |
| Environment | `action_space` | What the agent can do (tools, code-as-action) |
| | `observation_processor` | Transforms raw tool output before the model sees it (pipeline) |
| Judgment | `evaluator` | Scores a proposal, state, output or trajectory (multi-instance) |
| | `reflector` | Turns an evaluation into a reusable lesson |
| | `refiner` | Rewrites an output using a critique |
| State | `memory` | Stores: working, episodic, semantic, procedural (multi-instance) |
| | `context_manager` | Decides what stays in the window |
| Control | `controller` | Continue, stop, retry, decompose; owns budgets |
| | `search` | Explores alternatives (BFS/DFS, MCTS, beam, best-of-N) (exclusive) |
| | `aggregator` | Combines outputs (vote, select, synthesize) |
| | `topology` | Multiple agents and who talks to whom (exclusive) |
| | `curriculum` | Generates the next task (lifetime) |

**Cardinality:** exclusive slots allow one owner (a second claim is a conflict); pipeline
slots stack in declared order; multi-instance slots coexist under distinct ids.

## Scopes

`call` → `step` → `episode` (one attempt) → `task` (several attempts at one task) → `lifetime`.
The same slot type can appear at different scopes: Reflexion's evaluator runs per episode,
its reflector per task.

## Provenance

Every value in a spec records where it came from:
`stated` (paper/appendix) · `repo` (only in released code) · `inferred` (compiler's reading)
· `defaulted` (platform default). When a reproduction misses, `inferred` and `defaulted`
fields are the first suspects.

## Mechanism tests

Assertions over the execution trace that prove the mechanism is present, independent of
score. Example (Reflexion): after an episode fails, a reflection is written, and that
reflection text appears in the actor's prompt in the next episode. Scores drift with
models; mechanism tests don't. A compiled paper must pass its mechanism tests **before**
any benchmark number is reported, and a deliberately broken variant (fault seeding) must
**fail** them.

## Trace events

Scripts write semantic events to `run.trace`. Use exactly these names (they match
`traceEvent` in the schema): `episode.start`, `episode.end`, `step.start`, `step.end`,
`llm.request`, `llm.response`, `tool.call`, `tool.result`, `slot.emit`, `memory.write`,
`memory.read`, `env.snapshot`, `env.restore`, `controller.decision`.

## Agents are chains

An agent loop is a chain of slot steps (context builder → policy → action → observation →
evaluator → controller) that re-invokes itself until the controller says stop. Each slot is a
separate step so a paper's distinctive parts can be isolated, swapped and combined with
other papers'.

A paper with released code may first be bound **opaquely**: one step that runs its code
whole. That is legitimate when fidelity matters most, but the spec must mark it
`fidelity: partial` with a gap saying the paper hasn't been decomposed, since an opaque
step can't be composed with anything.

## MCP at two levels (none in v1)

1. **Inside a compiled agent:** MCP only for behavior the **model itself chooses to call**
   (action space, model-visible memory ops). Evaluators, reflectors, controllers and search
   are never exposed to the agent under test as tools, because that would let it skip or
   trigger them at will. The validator enforces this.
2. **Around the platform:** tools for the agents that *build and evaluate* agents (the
   compiler, later a self-improving designer): search the registry, validate a spec, run a
   chain, read traces, query evidence. Planned as RuleGo's MCP endpoint exposing selected
   chains. Every v1 script is tool-shaped (JSON Schema in and out) so this is wrapping, not
   rewriting.

## Registry

Reusable slot implementations, looked up before writing new code. Each is a script in
`registry/` and an `ad:Component` thing in the kernel with `ad:implements` assertions, so
finding a component for a slot is a kernel query, not a file lookup. Resolution order for
every slot: registry → paper's own code (licensed, pinned commit, behind an adapter) →
newly generated. A component that passes its contract tests is promoted into the registry.

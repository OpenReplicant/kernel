# OpenReplicant kernel — third iteration

A substrate for a class of entity: something with a persistent stream of experience that it
turns into beliefs about its world, recalls from, and acts on. Its knowledge lives in one
property graph with provenance and time; everything it does runs as RuleGo chains; and
capabilities arrive as modules of a few types (perceivers, reasoners, recallers, effectors,
ontologies, activated on change, on schedule or on command).

## Status

| Milestone | State | Where |
|---|---|---|
| S0 — spike: AgensGraph + pgvector, triggers → outbox → RuleGo | done | `docs/SPIKE_NOTES.md`, `spike/` (throwaway) |
| S1 — core conventions and modules | done | `docs/CORE.md`, `db/core.sql`, `kernel/` |
| S2 — the loop: one entity, one module of each type | done | `docs/LOOP.md`, `modules/climate*`, `world/` |
| S3 — model access: models as graph facts, budgeted and recorded calls | done | `docs/MODELS.md`, `modules/models`, `kernel/models.py` |
| Later (order to be decided) | — | `docs/PLAN.md`, `docs/BACKLOG.md` |

What exists now:

- **Knowing:** an AgensGraph graph (PostgreSQL 18 + openCypher) with pgvector. Claims carry
  method, confidence, source and span, valid time and recorded time; changes supersede, nothing
  is edited or deleted, and the database enforces it (triggers), not the callers. Claims link
  `derived_from` the claims they came from, so `trace()` walks a belief or an action back to
  the observations behind it.
- **Doing:** RuleGo-Server (v0.38.0 + `x/python`) runs every behaviour as a chain. Graph
  changes reach chains through a durable outbox and a bridge; a loop guard caps activation
  depth and fan-out. Shared chain nodes read, write (with provenance) and vector-search the
  graph.
- **Modules:** a manifest (`module.yaml`) declares types, ontology, chains, activations and
  vars; the loader installs labels with the kernel's triggers, records the module in the graph
  and deploys its chains.
- **A first entity:** a room-climate keeper on a simulated live stream. It observes readings,
  keeps a comfort belief, recalls it every second, decides, switches a heater, and observes
  the effect. A person can also tell it what they want in words: a model (Haiku through
  `claude -p` by default) interprets them into an inferred setpoint.
- **Models:** models, endpoints, prices and each module's model roles are claims in the graph;
  a shared node resolves a role's model from them, refuses calls over the role's budget, calls
  the endpoint (Anthropic API, any OpenAI-compatible server, or `claude -p` on a Claude Code
  subscription) and records the request, reply, answering model, tokens and cost as a source.

## Layout

| Path | What |
|---|---|
| `db/core.sql`, `db/agensgraph/` | the core schema and triggers; the database image |
| `kernel/` | Python: `db`, `claims`, `modules`, `bridge`, `rulego`, `models`, `providers`; shared chain nodes in `kernel/nodes/` |
| `rulego/` | the RuleGo-Server build and config |
| `modules/` | modules: `models` (model catalog and roles), the climate entity |
| `world/` | simulated worlds entities run against (not part of any entity) |
| `tests/` | the milestone gates; `tests/modules/notes` is the S1 test module |
| `tools/dev.sh` | start or stop the database container and RuleGo for development |
| `spike/` | S0 evidence, kept for reference |

## Running

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
KERNEL_GRAPH=test_kernel tools/dev.sh up     # needs dockerd; builds the database image once
export DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres KERNEL_GRAPH=test_kernel
.venv/bin/pytest                             # drops and recreates the test graph; 33 tests
```

`docs/LOOP.md` shows how to run the climate entity by hand. The model tests make a few small
real calls to Haiku through `claude -p` (they skip when `claude` is not on `PATH`).

## Docs

- `docs/DESIGN.md` — what the system is: store, runtime, module types, conventions
- `docs/PLAN.md` — milestones and their gates
- `docs/CORE.md` — the core as built: claims, provenance, the outbox and loop guard, modules
- `docs/LOOP.md` — the first entity: perceive, believe, recall, decide, act, perceive the effect
- `docs/MODELS.md` — model access: models as claims, roles, budgets, the recorded call
- `docs/SPIKE_NOTES.md` — facts about AgensGraph, pgvector and RuleGo, checked by experiment
- `docs/BACKLOG.md` — ideas parked until a milestone needs them
- `docs/LESSONS.md` — what the first two iterations taught, and what to carry over
- `CLAUDE.md` — working rules for Claude Code sessions in this repo

The second iteration (an evidence kernel in plain Postgres, RuleGo chains of pclib steps,
a component-graph compiler, a sandbox, model access through a budgeted sub-chain) is on the
branch [`second_iteration`](https://github.com/OpenReplicant/kernel/tree/second_iteration).
Its history is also this branch's history.

# OpenReplicant kernel — third iteration

A substrate for a class of entity: something with a persistent stream of experience that it
turns into beliefs about its world, recalls from, and acts on. Its knowledge lives in one
property graph with provenance and time; everything it does runs as RuleGo chains; and
capabilities arrive as modules of a few types (perceivers, reasoners, recallers, effectors,
ontologies, activated on change, on schedule or on command).

This branch starts over from a written design. S0 (spike) and S1 (core conventions and
modules) are done: `docs/SPIKE_NOTES.md`, `docs/CORE.md`.

- `docs/DESIGN.md` — what the system is: store, runtime, module types, conventions
- `docs/PLAN.md` — milestones, starting with a spike that tests the risky assumptions
- `docs/CORE.md` — the core as built: claims, provenance, the outbox and loop guard, modules
- `docs/SPIKE_NOTES.md` — facts about AgensGraph, pgvector and RuleGo, checked by experiment
- `docs/LESSONS.md` — what the first two iterations taught, and what to carry over
- `CLAUDE.md` — working rules for Claude Code sessions in this repo

The second iteration (an evidence kernel in plain Postgres, RuleGo chains of pclib steps,
a component-graph compiler, a sandbox, model access through a budgeted sub-chain) is on the
branch [`second_iteration`](https://github.com/OpenReplicant/kernel/tree/second_iteration).
Its history is also this branch's history.

## Running

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
KERNEL_GRAPH=test_kernel tools/dev.sh up     # needs dockerd; builds the database image once
export DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres KERNEL_GRAPH=test_kernel
.venv/bin/pytest
```

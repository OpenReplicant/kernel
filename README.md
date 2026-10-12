# OpenReplicant kernel — third iteration

A substrate for a class of entity: something with a persistent stream of experience that it
turns into beliefs about its world, recalls from, and acts on. Its knowledge lives in one
property graph with provenance and time; everything it does runs as RuleGo chains; and
capabilities arrive as modules of a few types (perceivers, reasoners, recallers, effectors,
ontologies, activated on change, on schedule or on command).

This branch starts over from a written design. Nothing is built yet.

- `docs/DESIGN.md` — what the system is: store, runtime, module types, conventions
- `docs/PLAN.md` — milestones, starting with a spike that tests the risky assumptions
- `docs/LESSONS.md` — what the first two iterations taught, and what to carry over
- `CLAUDE.md` — working rules for Claude Code sessions in this repo

The second iteration (an evidence kernel in plain Postgres, RuleGo chains of pclib steps,
a component-graph compiler, a sandbox, model access through a budgeted sub-chain) is on the
branch [`second_iteration`](https://github.com/OpenReplicant/kernel/tree/second_iteration).
Its history is also this branch's history.

# kernel

A general, domain-free kernel for an evidence-based world model: assertions about things and
how they are structured and connected, each traceable to the evidence behind it, whether that
is a document, a direct observation, a report or a session transcript.

Its first application is a compiler that turns agent-design papers into decomposed, runnable
systems, checks that each paper's mechanism is actually present, and records the results back
into the kernel as evidence.

This repository is currently a **starter scaffold**: design docs, the kernel and runtime
database schemas, vocabularies, the spec format, a worked Reflexion example and its validator.
Implementation follows `docs/V1_BUILD_PLAN.md`, milestone by milestone.

Start with:

- `docs/VISION.md` — why this exists
- `docs/KERNEL.md` — the kernel's constructs and interfaces
- `docs/V1_BUILD_PLAN.md` — milestones M0–M9 and their acceptance gates
- `CLAUDE.md` — working rules for Claude Code sessions building it

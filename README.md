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

## Running

```sh
python -m venv .venv && .venv/bin/pip install -e ".[test]"
podman compose up -d postgres            # or any Postgres 16; see .env.example
export DATABASE_URL=postgresql://kernel:kernel@127.0.0.1:5432/kernel
.venv/bin/pytest                         # each session creates and drops its own database
```

RuleGo (M0): `podman compose up -d rulego`, then
`RULEGO_URL=http://127.0.0.1:9090 RULEGO_DATABASE_URL=$DATABASE_URL .venv/bin/pytest tests/smoke` (see `rulego/README.md`).

Status: M0 (RuleGo), M1 (script contract), M2 (kernel core) and M3 (vocabularies, validation,
views, modules) gates pass.
Next: M4 (model calls with a budget).

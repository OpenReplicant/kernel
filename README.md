# OpenReplicant kernel

An **epistemic memory server** for agents: what an agent has been told, has seen and has
concluded, kept as claims with provenance and time in a property graph (AgensGraph +
pgvector), kept in order by processes of its own (belief under a trust policy, conflicts,
entity resolution, staleness, consolidation), and given back as small, cited context when it's
needed. Any agent or harness uses it over **MCP**; Claude Code gets hooks and a skill.

It is the memory, not the agent: running agents, writing code and building harnesses belong
to products built on it.

## Status

Fourth iteration, started afresh. Nothing is built yet; `docs/PLAN.md` starts with a spike
(M0). The previous iterations are on the `first_iteration`, `second_iteration` and
`third_iteration` branches; `docs/LESSONS.md` says what each taught and what to port.

## Docs

- `docs/DESIGN.md` — what the server is: the lifecycle (capture, encode, store, process,
  retrieve, feedback), interfaces, modules
- `docs/PLAN.md` — milestones and their gates
- `docs/LESSONS.md` — what went wrong and what worked before, cloud-session know-how
- `docs/BACKLOG.md` — ideas parked until a milestone needs them
- `docs/RELATED_WORK.md` — similar systems and papers, and proposed changes to the plan
- `CLAUDE.md` — working rules for Claude Code in this repo

# Plan

One milestone at a time. Each has a gate (tests that pass), reported with the commands run
and their output. Ideas that don't serve the current milestone go to `docs/BACKLOG.md`.

## S0 — Spike: the risky assumptions

Throwaway code under `spike/`, findings in `docs/SPIKE_NOTES.md`.

1. Run AgensGraph (image `skaiworldwide/agensgraph`, or build from
   github.com/bitnine-oss/agensgraph; v2.18.x is Postgres 18-based). Record version and how it
   was obtained.
2. Build and load **pgvector** on it; store a vertex with an embedding; run a nearest-neighbour
   query joined with a Cypher match.
3. Put a trigger on a vertex label and an edge label; check it fires per row on Cypher
   `CREATE`/`SET`/`DELETE` and can write an **outbox** row and `NOTIFY` in the same
   transaction.
4. Get outbox rows into a **RuleGo** chain: LISTEN endpoint if RuleGo has one, else a small
   bridge, else a schedule endpoint polling the outbox. Mark rows done; show no loss when the
   consumer is down and restarts.
5. A Python step reads and writes the graph from a chain.

**Gate:** a Cypher write fires a trigger → outbox row → RuleGo chain runs → the chain writes
back to the graph, and a vector + graph query returns the expected vertex. If AgensGraph fails
on pgvector or triggers, record it and decide (Apache AGE + pgvector on plain Postgres is the
fallback) before S1.

## S1 — Core conventions and modules

- Graph conventions from `docs/DESIGN.md` as code: claim nodes vs edges, time and provenance
  properties, supersede, the outbox, the loop guard.
- Module manifest and loader: installs an ontology, deploys chains, records the module in the
  graph.
- Shared chain nodes or scripts: graph read, graph write, vector search.

**Gate:** a test module loads; a claim written through it carries provenance and time; a
superseded claim is still answerable "as of" a past time; a write loop is stopped by the guard.

## S2 — The loop

One entity, one module of each type:
- a perceiver on a live stream (pick one that is easy to run in a container: a chat, a repo's
  activity, a sensor simulator);
- a small ontology;
- a reasoner activated on change that updates a belief;
- a scheduled poll;
- a recaller and an effector.

**Gate:** an action changes because of recalled state, and it can be traced back through
claims to the observations behind it.

## Later (order decided when S2 passes)

- Bulk ingestion: study, research, knowledge packs.
- Claude Code integration: hooks, MCP server, skill.
- Model access: models and endpoints as graph facts; local OpenAI-compatible endpoints and
  `claude -p`; budgets and tokens recorded.
- Sandbox for generated code (port from `second_iteration`).
- Modules describing themselves in the graph.
- Agent-design papers as modules (the second iteration's first application).

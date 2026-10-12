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

## S3 — Model access

Chosen first of the "Later" items: every other one (extraction for bulk ingestion, background
reasoners on a Claude Code session, code generation for the sandbox, papers) needs a model call.

- A `models` ontology: models, endpoints and model roles are things; what is known about them
  (model id, endpoint, API kind, prices, parameters, the env var holding a key) are claims.
- A catalog file loaded by a chain as `stated` claims with spans into it; a changed entry
  supersedes, an unchanged one writes nothing.
- A module's manifest names the model it uses for each role (a `defaulted` claim at install);
  choosing another model is a superseding claim, not a code change.
- A shared `model_call` node: resolves role → model → endpoint from the graph, refuses a call
  over the role's budget (USD or tokens, spend counted from the graph), calls the endpoint
  (`openai`-compatible, `anthropic` Messages API, `claude_code` = `claude -p`), and records the
  request, the reply, the answering model, tokens and cost as a `model_call` source. A failed
  call is recorded too.
- In the entity: a perceiver that turns a person's words into a setpoint through a model;
  the setpoint is `inferred` (the model interpreted what was said) and cites the call.

**Gate:** a person's sentence becomes an inferred setpoint through a model call resolved from
the graph, and the comfort belief derived from it traces back through the call to the words;
swapping the role's model changes the endpoint called; a role over budget is refused; tokens,
cost and the answering model are in the graph. The entity's model in the tests is real: Haiku
through `claude -p`; a local fake endpoint stands in only for API kinds that need a key or a
server this environment lacks.

## Later (order decided when S2 passes)

- Bulk ingestion: study, research, knowledge packs.
- Claude Code integration: hooks, MCP server, skill.
- Sandbox for generated code (port from `second_iteration`).
- Modules describing themselves in the graph.
- Agent-design papers as modules (the second iteration's first application).

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

## S4 — Sandbox and startup config

Untrusted code (code a model wrote, external code, benchmark tests) runs in a container. The
container runtime sits behind an MCP server, so swapping the runtime (Docker now; Podman,
Kubernetes, Nomad later, any of which is safer than Docker as root) touches only the server.
The MCP servers that exist for these runtimes don't enforce isolation and expose the whole
runtime (`docs/SPIKE_NOTES.md`, S4), so the interface is our own narrow server:

- An MCP server (`sandbox/`) with one tool, `sandbox_run(command, files, image, limits)` →
  `{exit_code, stdout, stderr, timed_out, duration_s, backend, isolated}`. Fixed in the server,
  not the caller: no network, read-only root, a small writable `/tmp`, the files read-only at
  `/work`, a non-root user, no capabilities, no new privileges, CPU, memory and process limits
  capped by config, a timeout that removes the container, images from an allowlist, nothing
  from the environment. Backends: `docker`, and `local` (a host subprocess with a timeout and
  an empty environment, no isolation) for when the sandbox is switched off.
- A startup config file (`config/kernel.yaml`, or `$KERNEL_CONFIG`): the sandbox (on/off,
  server, backend, images, limits) and the loop-guard limits. No secrets in it; environment
  variables still name the database and the graph.
- A shared `sandbox_run` chain node: an MCP client of the configured server; every run is
  recorded as a `sandbox_run` source (request, result, backend, whether it was isolated, what
  it was run for).

**Gate:** code run through the node can't reach the network, write outside `/tmp`, see the
host's environment, exceed its memory or process limits, or outlive its timeout (the container
is gone); each run is in the graph; with the sandbox switched off in config the same call runs
on the `local` backend and is recorded as not isolated. In the entity's terms: Haiku writes a
function for a small task, the sandbox runs the task's tests on it, and the observed result
traces back through the run to the model call that wrote the code.

## S5 — Model routing and agent harnesses (proposed)

- RuleGo's AI components (`rulego-components-ai`: `ai/llm`, `ai/agent`, `ai/mcpClient`) built
  into the server; where they fit next to `model_call` (they don't report token usage or the
  answering model as built, so recorded calls stay with `model_call`).
- LiteLLM: as an `openai`-kind endpoint (its proxy) and/or an API kind of its own (its SDK),
  so one catalog entry reaches any provider it routes to.
- Agent harnesses (Claude Code, Codex, …) behind one interface: one-shot, no tools, as
  `claude_code` is now; and agentic, with tools, inside the sandbox.

## Later (order decided when S2 passes)

- Bulk ingestion: study, research, knowledge packs.
- Claude Code integration: hooks, MCP server, skill.
- Modules describing themselves in the graph.
- Agent-design papers as modules (the second iteration's first application).

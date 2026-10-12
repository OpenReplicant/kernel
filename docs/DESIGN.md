# Design

## The idea

An **entity** has a persistent stream of experience. What it senses becomes observations;
observations are interpreted into claims; claims are computed over into beliefs about its
world, itself and what is about to happen; and beliefs drive what it recalls and what it
does. Its own actions come back to it as observations, so it can learn from what it did.

Language models are workers inside this loop, not the loop. The graph holds memory and
state and the chains hold control flow, so most model calls are narrow, single-shot jobs
over small recalled context (extract these claims, judge this, write this function). That
favours small and local models: many steps need no agentic skill at all.

## Two halves

| Half | What | Technology |
|---|---|---|
| **Knowing** | One property graph: things, claims, beliefs, observations, sources, the ontology; vector search on the same data; an outbox of changes that doubles as the backup log | **AgensGraph** (PostgreSQL fork with openCypher + SQL, triggers on graph data), with **pgvector** if it builds there (spike S0) |
| **Doing** | Every behaviour is a chain: perceivers, reasoners, recallers, effectors, wired from nodes that call Python scripts, executables, HTTP, models, MCP tools | **RuleGo-Server** (Go rule engine; nodes shape a script's I/O, so scripts need no special contract) |

The graph runs nothing. Chains reach it through a few shared nodes: graph read, graph write,
vector search.

## Module types

| Type | Direction | What it does | In RuleGo |
|---|---|---|---|
| **Perceiver** | world → graph | Senses observations and interprets them into claims. Raw observations are kept unchanged; interpretations can be revised | A chain from an input endpoint (HTTP, MQTT, file, schedule, …) through transforms to graph write |
| **Reasoner** | graph → graph | Derives and revises beliefs, consolidates, resolves entities, runs graph algorithms | A chain activated by a graph change or a schedule |
| **Recaller** | graph → context | Selects what is relevant now and renders it for a model or a person | A chain: graph and vector queries, ranking, rendering |
| **Effector** | graph → world | Acts: tools, messages, code, devices. Results return through a perceiver. Side effects need limits, approval rules and, for generated code, a sandbox | A chain ending in an action node |
| **Ontology** | — | Labels, properties, constraints the other types speak | Data loaded into the graph, enforced by DB constraints and triggers |

**Activations** start chains: **on change** (a graph trigger, delivered through the outbox),
**on schedule** (polling; RuleGo's schedule endpoint), **on command** (a person, a session or
another chain asks).

A **module** bundles these with a manifest (name, version, dependencies, what it contributes
of each type). Loading it installs its ontology into the graph and deploys its chains. An
**entity** is a deployed set of modules, shipped as a RuleGo container beside the database.
Add a new module type only when something real does not fit these five.

Later, modules and chains can be described in the graph itself (a module is a component, its
types are typed ports, wiring is edges), so an entity can describe and rearrange itself. Not
part of the core.

## Graph conventions (the core's real content)

- **Time and provenance on everything that can be wrong.** Claims carry `valid_from` /
  `valid_to` (when true in the world), `recorded_at` (when the entity learned it),
  `confidence`, the method (observed, stated, inferred, computed, defaulted) and a link to
  the source and the span inside it.
- **When a claim is a node.** An edge can't point at another edge. A plain structural fact is
  an edge with properties; a claim that needs its own evidence links, can be disputed or
  superseded, or is the subject of other claims is a node (`(:Claim)-[:ABOUT]->(thing)`).
  Decide per relation type in the ontology, not ad hoc.
- **No silent edits.** A change closes the old claim (`valid_to`, or superseded-by) and
  writes a new one, so "what did we believe at time T, and why" stays answerable.
- **Beliefs are computed.** What is currently held true comes from claims under a trust
  policy (sources, methods, recency), written by reasoners and recomputable.
- **The outbox.** Graph triggers write a row (what changed, causation id, depth) and
  `NOTIFY`; a consumer hands rows to RuleGo and marks them done. A bare NOTIFY is lost when no
  one listens and carries ~8 KB at most; the outbox is durable and is also the change log.
- **Loop guard.** Every activation carries a causation id and depth; reasoners that write can
  fire reasoners, so depth and fan-out are capped from day one.
- **Runtime guarantees live in the runtime.** Checkpoint/replay, tracing and model budgets are
  RuleGo aspects or wrapper sub-chains around steps, not a contract each script implements.

## Bulk ingestion ("I know kung-fu")

On-command activation of perceivers over a batch:

1. **Study** — point perceivers at a corpus (folder, URL list, repo, export). The batch is one
   session for provenance; entity resolution runs over the whole batch before writing;
   per-change triggers are held and one `batch done` event fires, so reasoners run once.
2. **Research** — a recaller → effector (search, fetch) → perceiver loop driven by a
   question, until it is answered or a budget runs out.
3. **Knowledge packs** — a module ships a pre-built subgraph with provenance intact; loading
   it installs the knowledge at once, still traceable and revisable.

## Running on a Claude Code session

The same entity runs headless (local or API models) or on top of an interactive Claude Code
session, which lets Pro users run it on their subscription:

- **Hooks are perceivers and recallers.** Prompts, tool results and session ends become
  observations; a prompt-submit hook adds recalled context before Claude sees the message.
- **An MCP server exposes the entity.** RuleGo-Server can serve chains as MCP tools: query,
  study, act.
- **A skill** teaches the session when and how to use them.
- **Background reasoners** use a local model (OpenAI-compatible endpoint) or `claude -p`
  (shown to work in the second iteration: no tools, no settings, empty working directory,
  reports tokens and the answering model).

## Open questions (answer with the spike, not by assumption)

1. AgensGraph: does pgvector build and work on it; how do triggers on vertex/edge labels
   behave; Python driver support; how close it tracks upstream Postgres.
2. How RuleGo consumes the outbox: a Postgres LISTEN endpoint if one exists, else a small
   bridge, else polling.
3. Embedding model for vector search (local vs API) and where embeddings are computed.
4. Trust policy for beliefs: the first one should be simple and explicit.

# Design

## What it is

An **epistemic memory server**: what an agent (or a person, or a program) has been told,
has seen and has concluded, kept as claims with provenance and time in a property graph, kept
in order by processes of its own, and given back as small, cited, relevant context when it's
needed. Any agent or harness reaches it through **MCP**. It holds knowledge; it does not run
the agent.

```
 agents, harnesses, people, programs
   │  MCP (tools, resources, prompts)        hooks (Claude Code first)       skills
   ▼
 ┌────────────────────────── memory server (Python, MCP SDK) ──────────────────────────┐
 │ capture ─▶ encode ─▶ store ─────▶ process (on change, on schedule) ─▶ retrieve ─▶ feedback │
 │                         ▲  outbox / change feed  │                                     │
 └─────────────────────────┼────────────────────────┼─────────────────────────────────────┘
                           │                        ▼
          AgensGraph + pgvector (the graph, full-text and vector indexes, SQL triggers)
```

**Not in it:** running agents for their own tasks, writing code or chains, sandboxes, tool
routing. Those belong to a harness built *on* this memory (`third_iteration` was a start at
one, on RuleGo). The server calls a model only for its own housekeeping (extraction,
consolidation, reflection), budgeted and recorded like any other source.

## The lifecycle, start to finish

Iterations one to three built capture and storage well and retrieval and upkeep barely
(docs/LESSONS.md). Each stage below is a first-class part of the product, with its own tools,
its own tests and its own measure.

### 1. Capture

What comes in, kept unchanged as a **source** (content-addressed, with where and when it came
from and who sent it): a message, a document, a tool result, a session transcript, a
correction from a person, a model's output. Nothing is interpreted at capture; a source is
evidence and is never edited.

### 2. Encode

Sources become **observations and claims**: `(thing) —predicate→ value | thing`, each with
method (`observed`, `stated`, `inferred`, `computed`, `defaulted`), confidence, the source
and the span inside it, the agent that wrote it, `valid_from`/`valid_to` (true in the world)
and `recorded_at` (when the memory learned it). Encoders are modules: some are rules (a
structured event → claims), some call a model (extract claims from text, with the span each
came from). An encoder's output that a person hasn't confirmed is `inferred`, never upgraded.

Encoding also **links**: which existing thing a mention refers to (entity resolution: by key,
by alias, by embedding, or left as a new thing with a "maybe same as" claim for later).

### 3. Store

The third iteration's core, ported: things, claims, sources, agents in AgensGraph; claims are
never edited, only superseded or retracted; the database stamps times and refuses claims
without provenance; every change writes an outbox row (the change feed and the backup log);
causation and depth on every write, with a loop guard. Plus what retrieval needs, kept current
by the database or the server: a full-text index over claim and source text, embeddings
(pgvector, HNSW) for things, claims and source chunks, and per-claim use counts.

### 4. Process (internal upkeep)

Processors keep the memory in order. They run **inside the server**, activated on change
(through the outbox) or on a schedule, and write only claims with provenance, so their work
is traceable and reversible:

| Processor | What it does | Needs a model? |
|---|---|---|
| **Index** | Embeddings and full-text vectors for new things, claims and source chunks | an embedding model |
| **Resolve** | Merge things that are the same (keys, aliases, embeddings): a `same_as` claim, not a destructive merge | sometimes |
| **Believe** | What is held true per subject and predicate, from competing claims under a **trust policy** (method, source, confidence, recency): a computed `belief`, recomputed when its inputs change | no |
| **Contradict** | Flag claims that can't both hold (same functional predicate, overlapping validity, different values) as an open conflict to resolve or to ask about | no |
| **Stale** | Lower or expire beliefs whose evidence is old for their kind (a temperature after an hour, a person's job after a year) | no |
| **Consolidate** | Episodes (a session, a day) into summaries and durable facts, each citing what it was made from | yes |
| **Sort** | Things into topics and clusters, so recall can follow topics as well as similarity | sometimes |
| **Reflect** | Questions the memory can't answer, gaps, patterns across episodes: proposals for an agent or a person, written as `inferred` | yes |

Deterministic processors come first and run in-process. Processors that need a model are
**agents** in the plain sense: code that calls a model with recalled context and writes what it
concludes, under a budget, each call recorded as a source.

### 5. Retrieve

The reason for the rest. Retrieval answers "what is relevant now" with a **context pack**:
a few held beliefs and claims, each with its value, confidence, method, source and age, ranked,
cut to a token budget, rendered for a model, with ids so a later answer can cite them.

- **Recall** (`recall(query, focus?, budget)`): hybrid candidates (full-text, vector, the
  query's resolved things and their graph neighbourhood, topics) ranked by relevance, belief
  strength, recency and use; explained (why each item made the cut).
- **Lookups**: a thing's current beliefs; a claim's `why` (source, span) and `trace` (what
  it was derived from, down to observations); `as_of` a past time; what changed since T.
- **Conflicts and gaps**: open contradictions about the focus; what's unknown or stale.

Retrieval is measured: a fixed benchmark (a corpus, questions, the claims that answer them)
reports recall@k and context-pack precision for every change to ranking, so "better" is a
number, not an impression.

### 6. Feedback

Use is evidence too. An agent reports which recalled items it used and which were wrong; a
person corrects a claim (a `stated` claim superseding it). Use counts feed ranking;
corrections feed the trust policy and the processors.

## Interfaces

- **MCP server** (streamable HTTP and stdio): tools for capture, claims, recall, lookups,
  feedback and admin; resources for things and claims by id; prompts for common recalls. A
  change subscription for clients that react to changes.
- **Hooks**, per harness, starting with **Claude Code**: session start and prompt submit
  recall into context; tool results, session ends and corrections are captured. No universal
  hook standard is assumed; each harness gets an integration (a Claude Code plugin: hooks,
  MCP config, skill).
- **Skills** (Agent Skills, `SKILL.md`): when and how an agent should remember, recall, cite
  and correct.
- **CLI** for people and scripts (ingest a folder, recall, why, export).

## Modules

What varies by domain or deployment is a module: a directory with a manifest, Python code
and SQL files, loaded at start-up.

| Type | What |
|---|---|
| **Ontology** | Thing labels, predicates (functional or not, claim or edge, typical staleness), constraints |
| **Encoder** | Source kind → claims (rules or a model) |
| **Processor** | On change or schedule: claims → claims (the table above; the built-in ones are modules too) |
| **Decoder** | Claims → a rendering: a context pack format, a report, an export |
| **Integration** | A harness's hooks and skill, an MCP client of another server (a source of observations) |
| **Knowledge pack** | A pre-built subgraph with its provenance, loaded as is |

## Packaging

`docker compose up`: AgensGraph + pgvector, and the memory server. Secrets (model keys) from
the environment. A config file for the server: models for embedding and housekeeping, budgets,
processors on or off, schedules, the loop guard's limits.

## Conventions carried over (non-negotiable)

- Provenance on every claim: source and span, method, confidence, agent, both times.
- No silent edits: supersede or retract; "what did we believe at T, and why" stays answerable.
- Beliefs are computed from claims under an explicit trust policy, never written by hand.
- Never upgrade a guess: model output is `inferred` until a person or an observation says otherwise.
- Every model call the server makes is recorded as a source, with tokens, cost and the model.

## Open questions (answer by experiment in the spike)

1. **Embeddings here:** huggingface.co is blocked in cloud sessions; which embedding model can
   the server run or reach (a pip-installable model with weights from elsewhere, an API with a
   key)? Full-text search (Postgres `tsvector`) works without one and is the fallback.
2. **MCP from Python over AgensGraph:** sessions, concurrency (a connection pool), streamable
   HTTP and stdio, change notifications to clients.
3. **Model calls for housekeeping:** `claude -p` (proven in iteration three), an API key, or
   MCP sampling (the client's model; does Claude Code support it?).
4. **The trust policy:** the first one simple and explicit, then measured.
5. **Which harness hooks** give the most for recall (prompt submit) and capture (session end,
   tool results), and their cost in latency.

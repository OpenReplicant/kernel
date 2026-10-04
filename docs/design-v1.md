# World Model Kernel — Design v1.0

Oct 3, 2026 · @Pete

## What it is

A lightweight world model any agent can plug into, built from conversations and data, that tracks what is believed, by whom, since when, and where sources disagree.

Agent memory tools answer "what was said?" This kernel answers more questions about every fact:

| Dimension | Question it answers |
| --- | --- |
| Provenance | Who or what asserted it, from which source and passage |
| Basis and trust | Was it observed, reported or inferred, and how much does that source weigh |
| Two clocks | When was it true in the world, and when did the system learn it |
| Modality | Does it describe, predict, require or propose |
| Belief | Do credible sources agree, or is it contested |
| Rules | Is it allowed at all under the ontology, enforced rather than flagged |
| Consequence | What should be done about it, through proposals and approvals |

**Shape of the product.** One Postgres database and one MCP gateway, started with `docker compose up` and usable from any agent harness. Every surface is an existing standard: Postgres for data, MCP for agents, Agent Skills for instructions, ACP for agent sessions, OTel for operations.

**Kernel first, products as packs.** The kernel ships as a product in its own right and is never shaped around a single product. Domains arrive as packs that float on versioned kernel interfaces.

**Lineage.** This design supersedes the [v0.1–v0.5 design spec](https://claude.ai/code/artifact/59d0c431-da9b-446a-81c6-770407fd5d08), which records how each decision was reached.

## How it works

Helpers read the map, then propose a claim with the graph changes it justifies; the librarian accepts it into the notebook and map in one commit, or hands it back with the reason.

&#91;embedded content: how it works · one gate, one transaction\]

- **Notebook (the log):** every claim with who said it, where and when. Never erased; corrections are new lines.
- **Map (the graph):** current state, updated in the same commit, and rebuilt exactly by replaying the notebook.
- **Librarian (`kernel.write`):** the only way in. It enforces the ontology rules, catches duplicates and stale reads, and explains every rejection.
- **Helpers:** any AI agent: a person's chat harness, bulk workers, or headless observer runs. They never edit the map directly.
- **Packs:** add-on lessons that teach the helpers a subject and give the librarian that subject's rules.

## Ontology

Four node types, a short list of kernel edges, and rules that every write must pass. A new node type is added only when the kernel must treat it differently; everything else is a property, an edge, or pack-level data.

| Node type | What it is | Key properties |
| --- | --- | --- |
| Entity | Anything that persists | `kind` (component, concept, data, source, symbol, role, process, activity, gateway, or pack-defined), `namespace` |
| Agent | A human or machine actor | `kind`, `trust_level`, `permissions`, profile for machine agents |
| Claim | A statement about other nodes or edges | `modality`, `basis`, `polarity`, `confidence`, `status`, `text` |
| Event | Something that happens, with participants and duration | `kind`, `start`, `end`, `status` |

**Modality:** descriptive, predictive, normative, proposed, hypothetical. Goals, requirements, rules and proposals are claims with a modality, not separate types. **Basis:** observed, reported, inferred.

**Kernel edges**

| Group | Edges |
| --- | --- |
| Structure | `part_of`, `instance_of`, `subtype_of`, `depends_on`, `implements` (entity or agent → role), `flows_to` |
| Identity | `same_as` (links duplicates; nodes are never merged), `denotes` (symbol → what it refers to) |
| Epistemic | `about`, `supports`, `contradicts`, `supersedes`, `refines`, `assumes` |
| Time and causation | `participates_in` (with a role), `precedes`, `causes` |
| Governance | `responsible_for`, `monitors`, `approved_by`, `rejected_by`, `verified_by` |

Packs specialize kernel edges (`reads_from` specializes `depends_on`); they never add kernel-level types or edges. `violates` is computed by query, never stored.

**Ontology rules.** Packs declare rules; the installer compiles them into checks that run inside the write transaction.

| Category | Constrains | Example |
| --- | --- | --- |
| Types | Kinds allowed per namespace | `bpm` allows process, activity, gateway, role |
| Domain and range | Which kinds an edge may connect | `flows_to`: activity → activity |
| Cardinality | Single-valued edges | One approver per role at a time |
| Time | No overlapping windows on single-valued edges | Dana's and Sam's approver windows cannot overlap |
| Identity | Properties that identify an entity | Email for a person |
| Provenance | Every operation cites a claim; some edges need a stronger basis | Measured values require `observed` |

Every kind and edge in a pack schema carries a label and a description; the installer refuses entries without them, because schema slicing depends on them.

**Standards alignment.** The kernel keeps its own vocabulary and maps to W3C PROV-O (provenance), SKOS (labels and hierarchies), OWL-Time, BPMN and OCEL 2.0 for import and export. External ontologies are imported as concept data in their own namespace, never as kernel schema.

## The write path

Every change to the log and graph enters through one function, `kernel.write(payload)`, in one transaction: the claim, the graph operations it justifies, the rule checks and the log append commit together or not at all. Sources and cite records enter through two sibling kernel functions, `kernel.ingest_source` and `kernel.cite`. No caller has any other way in; database permissions enforce it.

**The payload.** The model that read a claim also chooses the graph changes, resolving entities in context against the graph it has read:

```json
{"claim": {"text": "Sam took over invoice approval from Dana in March",
           "source": "chunk_0412_p3", "basis": "reported", "modality": "descriptive"},
 "read_at_offset": 48210, "trace_id": "4bf92f35...",
 "ops": [{"op": "assert", "edge": "implements", "from": "agt_sam", "to": "role_approver",
          "valid_from": "2026-03-01"},
         {"op": "assert", "edge_id": "e_dana_approver", "valid_to": "2026-03-01"}]}
```

**Operations:** `create`, `assert` (an edge or claim, including validity changes), `link` and `unlink` (`same_as`), `promote` (assertions into a claim node), `transition` (status), `redact`. There is no delete; retraction is a new assertion with opposite polarity, or a `supersedes`.

**What happens on write**

1. An advisory lock inside kernel.write serializes appends, so offsets commit in order with no gaps even with several gateway or worker processes.
2. Each operation is checked against the ontology rules.
3. If any node it touches changed after `read_at_offset`, the write is rejected as stale.
4. A `create` runs the resolution cascade; high-confidence duplicates reject the create and return the candidates.
5. The log entry and graph changes commit; deterministic triggers update derived state such as belief scores.

**Rejections are RFC 9457 problem documents**, so every harness gets the same shape:

```json
{"type": "urn:wmk:rule:cardinality", "title": "Single-valued edge conflict",
 "detail": "role_approver already has implements from agt_dana for 2026-03-01 onward",
 "rule": "bpm.approver_cardinality", "candidates": []}
```

The rejection names the broken rule and the nearest allowed kinds or edges, so the model can usually fix it on retry.

**Unresolved claims.** A claim that maps to no ontology term, or still fails after two retries, is stored with its text and provenance and no graph operations. It waits for a pack or concept formation to place it. Nothing true is thrown away for failing to fit.

**Determinism.** The log stores the operations the model chose, in kernel vocabulary, never a query and never an instruction to call a model. Rebuilding the graph by replaying the log reproduces it exactly; CI checks this on every change.

**Personal data.** Source content is encrypted per source; erasure deletes the key, and replay projects those fields as redacted. Each client gets its own database, with row-level security on namespaces.

## Belief and time

Current belief is a pure, versioned function of the assertions; it never changes unless the log does.

**The belief function**, for each edge or claim:

1. Group assertions by source; each source counts once, weighted by agent trust × basis (observed > reported > inferred) × calibrated confidence.
2. Denials weigh against.
3. Produce a score and a status: accepted, contested, rejected or unknown. Contested means credible sources on both sides.

- **Conflicts are the kernel's call, not the model's.** On a single-valued edge, a newer claim from the same source supersedes; a claim from a different source makes the fact contested. Contested facts are shown with both sources, never resolved silently.
- **No decay.** Belief does not weaken with age. Time-sensitive questions filter by timestamp at query time.
- **Re-extraction supersedes.** A new extractor version replaces the previous version's assertions from the same source rather than adding to them. Unchanged sources are skipped by content hash.
- **Raw model confidence is never used directly.** Until calibration exists from evals, it maps to low, medium or high.

**Two clocks.** Valid time (`valid_from`, `valid_to`) is when something was true in the world. Record time (`recorded_at`, offset) is when the system learned it. "What was true on March 1" and "what did we believe on March 1" are different queries, and both are supported. Conformance checking uses valid time; audit uses record time.

## The MVP

Five pieces and two ACP profiles, all in Python, each service its own container.

| Piece | Contents | Surface it exposes |
| --- | --- | --- |
| `core.sql` | Log, claims, assertions, graph (Apache AGE), sources and chunks, cite records, rules, `kernel.write`, belief triggers, roles | Any Postgres tool |
| Gateway | MCP server on the official Python SDK, with OTel spans | Any MCP harness |
| Core skill | Read the graph first, extract claims, reference entity IDs, handle rejections, cite | Any Agent Skills harness |
| `docker-compose.yml` | Database and gateway; optional profile with an OTel Collector and trace viewer | `docker compose up` |
| Evals | Sample sources with expected graphs; the replay test | CI |

**Database extensions:** Apache AGE (graph and Cypher), pgvector (embeddings), pg\_trgm (fuzzy name matching).

**Configuration.** The MVP needs no chat-model endpoint, because the harness does the thinking. It does need an embedding endpoint for schema slicing and resolution; without one, both fall back to names and trigram matching. The core schema is loaded by `core.sql`; the pack installer arrives in Phase 2.

**Gateway tools**

| Tool | Tier | Does |
| --- | --- | --- |
| `write` | Write | Submits a claim-plus-operations payload to `kernel.write` |
| `lookup_entities` | Read | Ranked resolution candidates for names and kinds |
| `get_schema_slice` | Read | The kinds, edges and rules most relevant to a passage |
| `query_graph` | Read | Read-only Cypher, with belief status and as-of on both clocks |
| `query_log` | Read | Log entries by source, agent, entity or time |
| `ingest_source` | Write | Stores a source, chunks it with spans, skips known content hashes; text and Markdown only until the parser container arrives |
| `cite` | Write | Records which assertions each answer sentence relied on |

**Database roles:** the gateway's writer role may only execute the three kernel write functions (`kernel.write`, `kernel.ingest_source`, `kernel.cite`); the reader role may only select; UI tools connect with the reader role, so watching the database can never bypass the log.

**ACP profiles.** Small config files describing one way to run a session. Each machine profile is also an Agent entity, so its writes are attributed to a known configuration.

| Setting | `interactive` | `eval` |
| --- | --- | --- |
| Purpose | Real use through an ACP client | CI runs of the core skill against eval fixtures |
| Permissions | Passed to the person | Policy: kernel tools only |
| Skills | Core + interview | Core + task skill |
| Identity | The person and the assistant as agents | One machine agent |
| Transcript capture | On, with consent; each turn is a citable event | Off |
| Limits | Generous | Strict timeout and budget |

Adding the MCP server and skills to any harness also works without ACP; the interactive profile adds precise transcript provenance.

## Ingestion

Ingestion is built around conversation and data, reproducing proven techniques from Graphiti, Cognee and TrustGraph (all Apache 2.0) inside the kernel rather than running them as services.

| Stage | Where it lives | Technique | Learned from |
| --- | --- | --- | --- |
| Sources and chunks | Kernel tables; parsing in a pack container | Stable chunk IDs with document, page and character span; skip known content hashes | Cognee, TrustGraph |
| Schema slicing | Kernel function over embedded kind and edge descriptions | Give each extraction only the relevant slice of the ontology | TrustGraph, Cognee, ODKE+ |
| Extraction | `extract-claims` skill | Entities with IDs first; relationships may only reference those IDs; reference-time rules for dates; optional reflexion pass for missed facts | Graphiti |
| Grounding | Ontology rules | Reject with helpful errors, retry, else store as unresolved | Cognee annotate and strict modes, improved |
| Resolution | `resolve_candidates` SQL function | Identity keys → normalized exact → pg\_trgm → pgvector; model decides only the ambiguous band | Graphiti |
| Structured data | Mapping-file adapter | Tables to kinds, foreign keys to edges, rows as observed claims, no model | Cognee |
| Answer provenance | `cite` records | Each answer sentence linked to its assertions | TrustGraph |

**One skill, three thinkers.** The `extract-claims` skill drives the harness in conversation (the chunk is a conversation turn), workers in bulk, and observer runs. Reflexion is a per-pack setting because it roughly doubles model calls.

**Resolution is a measured stage.** Benchmarks suggest it is the weakest stage across existing tools, so it gets its own eval set. A scheduled sweep proposes `same_as` links for duplicates that slipped through.

**One write path, always.** Bulk ingestion is many payloads through `kernel.write`, never a direct import. Graphiti's issue history shows what fast paths cost: unscoped invalidation retiring unrelated facts, non-overlapping intervals collapsing, and bulk loads skipping contradiction checks. Each becomes a kernel regression test.

**Flows**

- **Bulk:** `ingest_source` → chunks → one job per chunk → worker gets the schema slice, looks up entities, extracts with the skill, writes → rejected writes retried, then stored as unresolved.
- **Interactive:** the person talks or shares a file → the harness follows the same skill, calling `get_schema_slice`, `lookup_entities`, `write` and `cite` itself.

## Packs

A pack is a folder whose root is a valid Agent Skill, with kernel-specific parts beside it. Any skills-compatible agent can read its guidance; the kernel enforces its rules.

```
bpm-pack/
  SKILL.md          # Agent Skills frontmatter; kernel range in metadata
  schema.yaml       # kinds and edge specializations, each with label + description
  rules.yaml        # ontology rules, compiled at install
  sql/              # extensions, views, triggers in the pack's own schema
  skills/           # extraction, interview and view skills
  workflows/        # optional step definitions
  mcp/              # optional container wrapping an outside service
  evals/            # fixtures and expected graphs
```

Skills inside `skills/` are copied by the installer into the folders each harness scans, since harnesses do not discover skills nested inside another skill.

**Boundary rule.** Anything that enforces an invariant (provenance, determinism, trust, access, validation) is kernel. Anything involving model judgment or domain assumptions is a pack, even when generic.

| Layer | Contents |
| --- | --- |
| Kernel | Log, write function, rules engine, belief, resolution cascade, gateway, roles |
| Core utility pack, installed by default | Document adapter and parser container, `extract-claims` and interview skills, contradiction checks, PROV-O and SKOS export |
| Research pack | Paper adapters, scientific-claim extraction, field map; the public demo |
| Reference packs | Toy research, process and infrastructure domains; CI fixtures only |

**Pack SQL is scoped.** It may only create objects in the pack's own schema, runs under a pack role, and is applied by the installer, never by a model.

**Gateway as the single MCP server.** The harness connects to one server. Packs that wrap outside services ship a container; the gateway calls it as an MCP client and exposes its tools under the pack's namespace (`research.search_arxiv`). Pack containers never get database credentials.

**Registry v0.** A text file of GitHub URLs pinned to commit hashes. Installing fetches and verifies the hash, checks kernel compatibility, shows what the pack adds, waits for approval, applies its SQL, and logs the installed version.

**Compatibility.** Semantic versioning; packs declare the kernel range they support; nothing in kernel 1.x is removed or changed incompatibly; reference packs run in CI on every kernel change.

## Observability

OTel records how the system runs; the log records what the system believes. They share IDs and the log feeds OTel, but neither replaces the other.

| Concern | OTel | Log |
| --- | --- | --- |
| Purpose | Latency, errors, cost, call paths | Claims, provenance, belief |
| Guarantees | Sampled, batched, retention-limited | Every entry, exactly once, in order, permanent |
| Write feedback | None; fire-and-forget | Synchronous accept or RFC 9457 rejection |
| Retrieval traces | Yes: which assertions each read returned | No |
| Answer citations | No | Yes, as `cite` records |

**What the services emit:** spans following the OpenTelemetry GenAI and MCP conventions (MCP server spans, `execute_tool`, `invoke_agent` for headless runs, model calls), with W3C trace context propagated through MCP so an instrumented harness links to the gateway.

**Linking:** every log entry and cite record carries `trace_id` and `span_id`. From a claim you can open the run that wrote it; from a slow or failing run you can find every claim it wrote. After each commit the kernel also emits the entry as an OTel event, so one backend shows world-model changes beside operational traces.

**Metrics that feed the system:** tokens and cost per run (budgets), job queue depth, rejection rate per rule, and resolution band distribution.

**Two cautions.** The GenAI conventions are still in Development status and moved to a dedicated repository in June 2026, so all OTel naming lives in one module. Message content capture stays off: spans carry IDs that point into the log, never personal data.

**History view.** A `history` view merges log entries and cite records by time for a complete record of what was known and how it was used.

## Later layers

Each layer adds a new kind of writer or reader; none changes the log, the rules or the write path.

**Workers and observer runs.** The kernel cannot tell a chat session from a background run.

| Mode | Who thinks | Used for |
| --- | --- | --- |
| Interactive session | The harness's model | Mapping, interviews, questions (MVP) |
| Worker job | A configured model endpoint in a direct loop with the skill text | Bulk extraction and re-extraction |
| Observer run | A full harness started headless over ACP | Resolving contested facts, multi-step research, triage |

Observer runs use the Agent Client Protocol: a session is opened with a run workspace (skills placed where the harness scans) and the kernel gateway with a run-scoped credential. The observer answers permission requests by policy, enforces timeout and budget, and closes the session. Official ACP SDKs exist for Python, TypeScript, Rust, Kotlin and Java. Work items come from triggers (new unresolved claim, fact became contested, new source), schedules, webhooks and chat channels. The log is the coordination layer, a blackboard, so agents never need to talk to each other directly.

**Workflows.** Definitions are graph data on the BPM ontology, with step kinds tool, transform, decision, LLM (referencing a skill) and human (through approvals). Runs are events with outputs as claims, so conformance checking works on agent workflows too. Execution is a slot: a lightweight native runner on the job queue, or adapters to an engine the customer already uses (Temporal, Airflow, n8n, a BPMN engine). A skill folder may include a workflow file beside SKILL.md.

**The ops loop:** observe → evaluate → respond → verify → promote, applied to software components or business processes alike. Strategies escalate from playbooks to search to generative proposals. The kernel stays passive; all actuation lives in a permissioned ops pack, and nothing reaches production without approval.

| Need | Standard | Adopt in |
| --- | --- | --- |
| Error responses | RFC 9457 Problem Details | MVP |
| Goals and thresholds | OpenSLO | Ops pack |
| Events and alerts | CloudEvents | Ops pack |
| Response playbooks and workflow definitions | Serverless Workflow; CACAO for security | Ops pack |
| Rollouts and swaps | OpenFeature | Ops pack |
| Process change simulation | BPSim with BPMN | BPM pack |
| Incident and change vocabulary | NIST SP 800-61, ITIL | Ops pack |

Upgrade testing has no formal standard; the practice is progressive delivery. A proposal's `verified_by` points to a canary event whose observed results are checked against goals imported from OpenSLO.

## Toward self-maintenance

Three features let the system maintain and improve itself, autopoietic within a constitution: it may change its structure, but its organization (kernel invariants, evals, setpoints, the approval channel) stays in human hands. Every step produces proposals, never direct changes.

| Cognitive function | Component |
| --- | --- |
| Episodic memory | The log |
| Semantic memory | The graph |
| Concepts | The ontology |
| Procedural memory | Skills and workflows |
| Epistemic judgment | The belief function |
| Perception | Ingestion: conversation, documents, OTel |
| Attention | Work queue and vital-sign priorities |
| Interpretation | LLM agents across harnesses |
| Executive control | Human approval and rule rejection |
| Habit formation | Compiling repeated runs into workflows |

**1. Vital signs (homeostasis).** SQL views compute contested rate, unresolved backlog and age, rejection rate per rule, ambiguous resolution rate, coverage gaps, staleness of state-like facts, human correction rate and cost per accepted claim. Each has a human-set setpoint stored as a normative claim about the system. Drift creates work items, ranked by distance from setpoint × importance of the affected graph × likelihood of success ÷ cost, within a fixed maintenance share of the budget. Vital signs are measured by the kernel, never reported by the agents being judged, and a random sample of maintenance work gets human audit.

**2. Concept formation.** Unresolved claims mark where concepts run out. A worker clusters them by embedding and failed rule; a cluster qualifies only when large enough and drawn from several independent sources. A model drafts the smallest change that places it, the change is tested by re-extracting in a shadow copy, and a person approves it with that evidence. Risk tiers, lowest first: alias, property, edge specialization, entity kind, rule. Learned concepts live in a versioned learned namespace and can graduate into a pack.

**3. Habit formation.** Runs are grouped by task signature from OTel spans and linked log entries. When enough runs share structure and good outcomes, they are compiled into a workflow following TraceCompiler's approach: each argument traced to a constant, input, prior output or transform, with only underivable values left as LLM steps. The workflow runs in shadow beside the LLM path, is promoted with approval, and falls back to the LLM path when a step fails its postcondition; repeated fallbacks trigger recompilation.

**The cycle:** vital signs decide what needs attention, concept formation grows understanding, habit formation makes repeated work cheap, and freed budget returns to maintenance. Approval load stays manageable through batched reviews and a human-set policy that may auto-approve the lowest-risk tier.

**Build order:** vital signs first (pure SQL, useful as a dashboard immediately), concept formation once unresolved claims accumulate, habit formation once tasks have run many times.

## Prior art and benchmarks

The log-plus-graph-over-MCP core already exists in several forms; the differentiation is epistemics, weight and harness-native packaging.

| Project | Strength | Difference from this kernel |
| --- | --- | --- |
| [Cognee](https://github.com/topoteretes/cognee) | Broad ingestion, Postgres-only mode, OWL grounding, MCP | Extraction inside its own pipeline; memory-focused; no append-only claim log or contested status in its docs |
| [TrustGraph](https://github.com/trustgraph-ai/trustgraph) | RDF 1.2 reification, OWL compliance, context cores, provenance | Heavy multi-engine enterprise platform; owns its agent runtime |
| [Graphiti](https://github.com/getzep/graphiti) | Bi-temporal edges, contradiction invalidation, MCP | Own LLM in the server; deletable edges; latest-wins invalidation |
| [knowledge-graph-rdbms](https://github.com/cunicopia-dev/knowledge-graph-rdbms) | Event log with graph as projection, write gate | Small; no belief, rules or packs |
| [agent-graph-memory](https://github.com/ZibbyDev/agent-graph-memory) | Claimed versus observed provenance | Memory only |
| Datomic, XTDB | Immutable assert/retract; bitemporal storage | Databases, not agent systems |

**Positioning.** Cognee is memory for agent developers; TrustGraph is an enterprise context platform with traceability. This kernel is lightweight (one database, any harness), epistemic (who believes what, how strongly, on which clock, where sources disagree) and harness-native (skills and MCP rather than a platform agents move into). Tools like Cognee can be sources rather than competitors.

**Benchmark plan.** Same model and same documents for every system.

| Task | Measure | Baseline |
| --- | --- | --- |
| Extraction quality | Entity and relation precision and recall against a gold graph | Cognee, Graphiti |
| Question answering over sources | Answer accuracy | Cognee |
| Entity resolution | Precision and recall on a dedicated set | Graphiti |
| Cost and latency | Per document and per query | Cognee |
| Provenance | Share of answer sentences traced to a source | TrustGraph |
| Point-in-time questions | Correct answers on valid and record time | Kernel only |
| Conflicting sources | Contested facts surfaced with both sources | Kernel only |
| Rule enforcement | Invalid writes rejected with the rule named | Kernel only |
| Replay | Graph rebuilt exactly from the log | Kernel only |

Fixtures include synthetic companies with known processes and simulated interviews containing contradictions and vagueness, so ground truth exists without client data.

## Product index

Every product starts by turning a world scattered across documents, systems and people's heads into one map that knows where each fact came from, when it was true, and who disagrees. The map is the beginning; once a world is mapped it can be questioned, compared, predicted, acted on and improved.

1. **Map.** Gather what is known into one graph, with sources.
2. **Understand.** Ask questions and get answers with receipts.
3. **Find gaps.** Surface what is unknown, unowned, unsourced or contested.
4. **Compare.** Intended against actual, documentation against reality, last quarter against this one.
5. **Predict.** Trace impact: what breaks, slows or shifts if one thing moves.
6. **Act.** Propose changes, test them in simulation, promote them with approval.
7. **Improve.** Watch the outcome; every change becomes evidence for the next.

| Product | What it maps | Where the map leads |
| --- | --- | --- |
| Research mapper | Claims, methods, contradictions and open questions in a field | Reviews that keep themselves current; hypotheses ranked by evidence |
| Business process mapping | How work actually flows: steps, owners, systems, handoffs | Conformance against intent; automation candidates; BPA with simulated before and after |
| Infrastructure and software ops | Services, dependencies, costs, owners, incidents | Blast-radius analysis; safe cleanup; approval-gated auto-ops |
| Codebase understanding | Modules, data flows, business rules, who knows what | Change-impact answers; docs that flag their own drift |
| Organizational operating model | Ownership, decisions and their reasons | Warnings before teams collide; memory that outlasts departures |
| Supply chain risk | Suppliers, their suppliers, regions, commodities | Exposure traced several tiers deep |
| Compliance mapping | Regulations, obligations, affected processes | Impact analysis when a rule changes; audit trails |
| Due diligence | A target's products, customers, dependencies, risks | Contradictions between claims and documents |
| Vocabulary and learning | A domain's symbols, jargon, prerequisites | Onboarding into a company's or field's language |
| Worldbuilding | Factions, places, characters, timelines | Consistency checks; what a faction would do next |
| Agent memory and grounding | Whatever an agent works on | Reasoning from sourced, versioned facts |

**First candidates:** Research and BPM, both self-serve by default with hands-on services on request. Each gets its own product spec, written as a pack against the kernel interfaces.

## Build order and open questions

Phase 1 builds only the five MVP pieces and two profiles; each later phase starts when its gate is met.

&#91;embedded content: build order · 3 phases, 2 gates\]

The MVP is done when a fresh `docker compose up`, connected to a harness, maps a sample domain from a conversation using a reference pack, the eval profile passes in CI, and replaying the log reproduces the graph exactly.

**Open questions**

- [ ] Demand: which product pulls hardest on the landing pages and calls, and at what price?
- [ ] Extraction quality on real interviews and documents: correct modality and confidence?
- [ ] Resolution accuracy on real data, against the dedicated set.
- [ ] Cost per document and per interview hour.
- [ ] Belief calibration across sources.
- [ ] Which harness the eval profile uses first.
- [ ] Attribution policy for interviews: aggregate by default, who sees individual sources.
- [ ] Model provider data terms for interview content.

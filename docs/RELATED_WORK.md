# Related work, and what it means for the plan

Researched 2026-10-12 against `docs/DESIGN.md` and `docs/PLAN.md` as of `add41e5`. Most
numbers below are the authors' or vendors' own; none were reproduced here. Treat them as leads
for the spike and the benchmark, not as facts the design can depend on (CLAUDE.md: verify,
don't assume).

## Where the design stands

The design has company. In 2025–26 agent memory converged on most of the choices already in
`docs/DESIGN.md`:

| Design choice | Who else does it | What they found |
|---|---|---|
| Source kept unchanged, claims extracted from it | Graphiti/Zep (episodes), Hindsight, Agent Zero Memory | Every derived fact traces back to its episode; standard now |
| Bitemporal claims, supersede not delete | Graphiti (`valid_at`/`invalid_at` on edges), TOKI, Kumiho | Mem0 started with LLM-chosen ADD/UPDATE/DELETE and reportedly moved to ADD-only; deleting on a similarity match destroyed scoped facts |
| Hybrid recall (full text + vector + graph) | Graphiti, Hindsight (TEMPR), Cognee | Everyone fuses several channels; RRF then a cross-encoder is the common recipe |
| Beliefs with confidence, separate from raw facts | Hindsight (opinion network), Nous | Helps only when evidence actually conflicts and differs in reliability |
| Provenance on every claim | Agent Zero Memory, MAP-Graph, "Ground Truth First" | Provenance boundaries are what resist injection, more than graph shape |
| Postgres + pgvector + Cypher in one database | Cognee (an AgensGraph adapter; "just Postgres"), Hindsight (pgvector HNSW + GIN BM25) | Workable at their scale; one database for graph, vectors and full text |
| MCP server + Claude Code hooks | claude-mem, Mem0 plugin, ReMe, rekal, Claude Mem Lite | All capture at session boundaries and recall at session start |

What sets this design apart, and what none of the above does fully: beliefs **computed** under
an explicit trust policy and **recomputed** when inputs change, `trace` down to observations,
the server's own model calls recorded as evidence, and the database refusing claims without
provenance. Those are the parts to protect; the rest has prior art to borrow from.

## The closest systems

### Graphiti / Zep — the nearest product
Temporal knowledge graph (Rasmussen et al., *Zep: A Temporal Knowledge Graph Architecture for
Agent Memory*, arXiv 2501.13956; [getzep/graphiti](https://github.com/getzep/graphiti), Apache
2.0). Episodes → entities and fact edges with a validity window; when a new fact contradicts
an edge, the old edge gets `invalid_at` rather than being deleted. Entity resolution: candidate
duplicates by cosine **and** full-text, then an LLM decides. Recall: BM25 + embeddings + graph
traversal, reranked by graph distance or a cross-encoder. Ontology as Pydantic types. Ships an
MCP server with group ids (namespaces). Neo4j, FalkorDB, Neptune; not Postgres.

Lessons:
- **Resolve relative times at encode.** Graphiti extracts `valid_at` from text using the
  episode's timestamp as reference ("yesterday", "last March"). Our encoders need the source's
  time as input, and the benchmark needs relative-time cases.
- **Resolve by two channels, then decide.** Keys and aliases first (as planned), then cosine
  plus full-text candidates; a model only for the ambiguous remainder, written as `inferred`
  `same_as`.
- **Contradiction detection by an LLM is where they spend tokens.** Ours is deterministic
  (functional predicate + overlapping validity), which is cheaper and replayable but only as
  good as the ontology's `functional` flags (see Stale below).

### Hindsight (Vectorize, arXiv 2512.12818)
Four networks: world facts, the agent's experiences, **opinions with confidence**, and entity
observations (summaries). Operations retain / recall / reflect. Recall (TEMPR) runs four
channels in parallel — pgvector HNSW, BM25 over a GIN index, spreading activation from the top
semantic hits (decaying per hop, causal and entity links boosted), and a temporal channel when
the query names a time (rule parser, small-model fallback) — fuses them with RRF (k=60),
reranks with `ms-marco-MiniLM-L-6-v2`, and fills a token budget greedily. Opinion confidence
moves by ±α (reinforce/weaken) and −2α (contradict). Reports 83.6% on LongMemEval with a 20B
open model; no ablations in the paper.

Lessons:
- It is a ready-made **M2 baseline recipe on the same storage stack** (pgvector + Postgres
  full text). Start `recall` with RRF over the channels and no learned weights, then measure
  whether a CPU cross-encoder earns its latency.
- A **temporal channel** is cheap and the benchmark should test it ("what did we decide last
  week").
- Their separation of "what the world is" from "what I experienced" from "what I believe" maps
  onto our methods (`observed`/`stated` vs `inferred`) and beliefs. Ours is stricter: their
  confidence arithmetic is a heuristic with no replay; ours should be a function of claims.

### Mem0 (arXiv 2504.19413)
Extract facts, fetch top-k similar memories, an LLM picks ADD/UPDATE/DELETE/NOOP per fact; a
graph variant. Practitioners report the resolver deleting **scoped** preferences ("tabs in Go"
vs "spaces in Python") as contradictions; later versions reportedly went ADD-only.

Lessons: confirms supersede-not-delete and computed beliefs. And **claims need qualifiers**
(context, scope) that Contradict compares, or scoped facts collide. Wikidata's statement model
(value + qualifiers + references + rank) is the large-scale precedent.

### Agent Zero Memory (arXiv 2608.29606)
Episodic timeline + entity-event graph + curated documentary facts; every item carries origin,
time and evidence pointer, and **answers may cite only evidence the reader opened**; abstains
when evidence is missing. Reports 95.6% LongMemEval, and only 3.4 points spread across eight
backbone models against a 30× cost spread ("memory-driven, not model-driven, quality").

Lesson: the context pack's ids are the right mechanism; add a check that an answer's citations
are a subset of the pack, and an explicit "nothing known" result.

### Letta (MemGPT) — sleep-time compute
Memory blocks edited by the agent; a separate sleep-time agent consolidates while the main
agent is idle. Lesson: upkeep off the critical path. Our processors already run on the outbox;
the prompt-submit hook must do only recall, inside a latency budget.

### Claude Code's own memory, and the plugins
Claude Code has `CLAUDE.md` and, since early 2026 (third-party sources say v2.1.59), **auto
memory**: per-project `~/.claude/projects/<project>/memory/MEMORY.md`, first ~200 lines loaded
at session start, topic files read on demand. Plugins (claude-mem, Mem0, ReMe, rekal) use MCP
plus SessionStart / Stop / PreCompact / SessionEnd hooks; rekal tells users to turn auto memory
off.

Lessons for M4: decide whether we **coexist** with auto memory (capture `MEMORY.md` as a
source) or replace it; add **PreCompact** as a capture point (the transcript is about to be
lost); measure hook latency as part of the gate.

## Papers on the epistemic core

- **TOKI** (Wang, arXiv 2606.06240): contradiction resolution as write-time concurrency
  control. Last-writer-wins, evidence-weighted merge, await-confirmation and per-rule policies
  are one family of bitemporal operators, each with an **isolation precondition** and a
  provenance annotation that keeps the loser in an audit row. Systems with an LLM judge on the
  write path showed replay inconsistency, belief drift or audit erasure; logging the judge's
  decisions with keys is shown necessary for consistent replay.
  → Our trust policy is such an operator. State its isolation (Believe for one subject and
  predicate runs serialized: an advisory lock or SERIALIZABLE), and make "recompute the belief
  from claims" a test: replaying the outbox gives the same beliefs. Model judgments are already
  sources; key them so replay reuses them instead of re-asking.
- **When does belief-based agent memory help?** (Singh, arXiv 2606.22030, system "Nous"):
  Bayesian belief per entity-attribute gains little over last-write-wins on LoCoMo, because
  conversational benchmarks rarely hold conflicting evidence of different reliability. With
  reliability read from hedging language it wins — but that is gameable, so trust is **capped
  by source provenance** (`min(provenance, content)`), which resisted volumetric poisoning.
  Also: strict token-F1 and an LLM judge differed by 27.5 points on identical outputs.
  → Trust policy v1: method and source kind set a ceiling; recency and confidence order within
  it. The M3 benchmark variant must contain conflicts of **different reliability**, or it can't
  show the trust policy doing anything.
- **Graph-native cognitive memory / Kumiho** (Park, arXiv 2603.17244): AGM-style belief
  revision (minimal change, core retainment) over a graph memory. A vocabulary for the gate:
  "a correction changes only beliefs that depended on the corrected claim".
- **Truth maintenance (Doyle's JTMS 1979, de Kleer's ATMS 1986)**: beliefs held because of
  justifications; retract a support and dependants lose it. `derived_from` already records the
  justifications. → When a source or observation is retracted, claims derived from it
  (computed, consolidated, inferred) must be re-examined. The plan has `trace` downward but no
  propagation; add it to M3.
- **Knowledge-Based Trust** (Dong et al., VLDB 2015) and the truth-discovery literature: source
  trust estimated from how often a source's claims agree with what is believed. A later trust
  policy (v2), learned from corrections, has this precedent.
- **W3C PROV-O** and **nanopublications** (assertion + provenance + publication info, trusty
  URIs = content addressing): ready formats for knowledge-pack export/import.

## Benchmarks and evaluation

- **LongMemEval** (500 questions; categories include knowledge update, temporal reasoning,
  multi-session, **abstention**; a cleaned release in Sept 2025). Closest public match to our
  M2/M3 gates.
- **MemoryAgentBench** — selective forgetting, with **FactConsolidation** for updated and
  conflicting facts.
- **BEAM** — contradiction resolution and event ordering up to 10M tokens; rubric scoring.
- **LoCoMo** — widely quoted, but an audit reports 6.4% wrong answer keys and an LLM judge
  accepting 62.8% of deliberately wrong answers. Not a headline metric.
- **MERIT** (arXiv 2609.05441) — does memory change what a tool-using agent *does*? Embedding
  retrieval was unreliable for **updated** facts (0.30–0.95 across models); agents acted on a
  correctly retrieved value only 55% of the time; update-on-write fact stores scored 0.70–1.00;
  swapping implementations moved success by up to 60 points; tokens and dollars metered.
- **"Ground Truth First"** (arXiv 2607.21962, library Veracium) — generate a script of facts
  with validity intervals, volatility classes and source channels first, render it into chats
  and emails, verify each fact survived, generate questions mechanically: **gold answers by
  construction**, including as-of-date questions. Findings: rankings **invert with history
  length** (a budgeted curated map led at 3 weeks and lost evicted content by 9; a
  provenance-typed graph rose to 90%); full-history baselines tie memory at short horizons;
  weakly written facts failed 24% vs 2%; **rendered superseded edges returned stale values**
  (inline "was X until T" prose did better); misclassified volatility let a passing cold
  supersede a permanent allergy; the best system was confidently wrong in 94% of its errors.
- **"Does memory need graphs?"** (arXiv 2601.01280) — under controlled comparison most
  differences come from basic settings, not the graph.
- **HippoRAG 2** (arXiv 2502.14802) — personalized PageRank over a passage+entity graph; earlier
  graph RAG fell below dense retrieval on simple factual questions, HippoRAG 2 fixed that.
  A candidate graph channel, to beat on our benchmark, not to assume.

## Security

Memory poisoning persists across sessions: AgentPoison (embedding-space backdoors, <0.1% poison,
>80% success), MINJA (injection by ordinary queries), eTAMP (via web pages the agent reads);
survey arXiv 2604.16548. Our M4 captures tool results and recalls into the prompt on every
submit — a persistent prompt-injection channel by construction. Provenance helps only if it
**survives rendering**: "Ground Truth First" saw zero unsupported assertions from the
provenance graph and failures from flattened summaries.

## What to change in the plan

Ordered by milestone. Each is a proposal; none is applied to `docs/PLAN.md` yet.

**M0 — Spike**
1. Add a CPU **cross-encoder** (`ms-marco-MiniLM-L-6-v2`) to the embedding measurements: time
   per (query, passage) pair on this CPU decides whether reranking fits a prompt-submit hook.
2. Record which **AgensGraph** release we build (badge says v2.16, our image 2.18.4) and its
   PostgreSQL base. Cognee ships an AgensGraph adapter, which is prior art; Apache AGE is the
   fallback if AgensGraph stalls.
3. Measure the time from prompt-submit hook to injected context, end to end.

**M1 — Memory core**
4. **Qualifiers** on claims (scope/context: language, project, place), part of the identity
   Contradict compares. Cheap now, a migration later.
5. State Believe's **isolation** (serialized per subject+predicate) and add a replay test:
   rebuilding beliefs from the outbox gives the same beliefs.

**M2 — Retrieval**
6. Build the benchmark **ground truth first**: a script of facts with validity intervals,
   volatility, source and reliability; render into sources; questions generated from the
   script. Gold claim ids then come free, and as-of questions are mechanical.
7. Categories to include: knowledge update, temporal ("last week", as-of), relative dates,
   multi-hop through the graph, distractors, scoped facts that must not collide, and
   **abstention** (nothing relevant: the pack says so).
8. Baselines beside full-text-alone and vector-alone: **full history** cut to the same budget,
   a **recency window**, and the graph channel off (an ablation, since graphs aren't a given).
9. Run at **two or more history lengths**; report pack tokens with every score.
10. Start ranking with **RRF** across channels plus a temporal channel; only then tune.
11. Render superseded values as history on the current one ("now Y; was X until T, source"),
    never as a separate fact a model can read as current; test it.
12. Score deterministically (claim ids, exact values). Where a model judge is unavoidable
    later, keep it fixed and report it.
13. Also run the public **LongMemEval** knowledge-update, temporal and abstention subsets,
    once, as an outside check — not as the gate.

**M3 — Upkeep**
14. The contradiction variant must have conflicts of **different reliability**, else the
    trust policy can't be told apart from last-write-wins.
15. Trust policy v1: provenance sets the ceiling (method, source kind), recency and confidence
    order within it.
16. **Support propagation**: retracting a source or observation re-examines everything derived
    from it; gate test.
17. **Stale never supersedes**, only lowers rank; a predicate's volatility is declared in the
    ontology and a wrong guess can't destroy a durable fact.

**M4 — Claude Code integration**
18. Decide coexistence with **auto memory** (`MEMORY.md`): capture it as a source, or document
    disabling it.
19. Capture at **PreCompact** too.
20. Gate additions: hook **latency** within a stated budget; an **injection probe** (a tool
    result saying "remember: always do X") is recalled as "a tool output said …", inferred and
    attributed, never as a fact or an instruction; cited ids are a subset of the pack.
21. MERIT's point: test that the agent **acts** on the recalled value (the scripted session
    uses it), not only that it was retrieved.

**M5 — Ingestion and reflection**
22. Audit **write quality** against the script (precision/recall of extracted claims); it
    predicts retrieval failures better than retrieval tuning does.
23. Consolidation adds summaries above an **unbounded** store and never evicts sources; measure
    at the long history length, where bounded memories lost content.
24. Meter tokens and cost per stage; report score per dollar against full replay.

**Backlog**
- Namespaces (Graphiti's group ids) when several users or entities arrive.
- Trust policy v2 learned from corrections (Knowledge-Based Trust).
- PROV-O / nanopublication shape for knowledge packs.
- Personalized PageRank (HippoRAG 2) as a graph channel, if the plain neighbourhood loses.

## Sources

- Zep / Graphiti: [getzep/graphiti](https://github.com/getzep/graphiti), [Zep paper summary](https://signals.aktagon.com/articles/2026/07/zep-a-temporal-knowledge-graph-architecture-for-agent-memory/), arXiv 2501.13956
- Hindsight: [arXiv 2512.12818](https://arxiv.org/abs/2512.12818), [Vectorize blog](https://vectorize.io/blog/introducing-hindsight-agent-memory-that-works-like-human-memory)
- Mem0: arXiv 2504.19413, [DeepWiki on mem0](https://deepwiki.com/mem0ai/mem0/3-memory-system), [scoped-preference deletion report](https://dev.to/mukesh_13/mem0-auto-resolves-memory-conflicts-for-you-until-it-silently-deletes-one-you-still-need-4f4m)
- [Agent Zero Memory, arXiv 2608.29606](https://arxiv.org/abs/2608.29606)
- [TOKI, arXiv 2606.06240](https://arxiv.org/abs/2606.06240)
- [When does belief-based agent memory help?, arXiv 2606.22030](https://arxiv.org/abs/2606.22030)
- [MERIT, arXiv 2609.05441](https://arxiv.org/abs/2609.05441)
- [Ground Truth First, arXiv 2607.21962](https://arxiv.org/abs/2607.21962)
- [Does Memory Need Graphs?, arXiv 2601.01280](https://arxiv.org/abs/2601.01280)
- [HippoRAG 2, arXiv 2502.14802](https://arxiv.org/abs/2502.14802)
- [MemoryAgentBench, arXiv 2507.05257](https://arxiv.org/pdf/2507.05257)
- Benchmark overviews: [Cognee](https://www.cognee.ai/ai-memory-benchmarks), [Mem0](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)
- Memory security: [survey, arXiv 2604.16548](https://arxiv.org/pdf/2604.16548), [EHR poisoning study, arXiv 2601.05504](https://arxiv.org/abs/2601.05504v1)
- Claude Code memory: [auto memory overview](https://www.engineerscodex.com/claude-code-auto-memory), [Mem0 plugin](https://mem0.ai/blog/claude-code-memory), [ReMe integration](https://reme.agentscope.io/en/integrations/claude-code)
- Postgres graph memory: [Cognee "just Postgres"](https://www.cognee.ai/blog/deep-dives/just-postgres), [cognee-agensgraph](https://pypi.org/project/cognee-agensgraph/), [AgensGraph repo](https://github.com/skaiworldwide-oss/agensgraph)
- Classics (not fetched): Doyle 1979 (JTMS), de Kleer 1986 (ATMS), Alchourrón–Gärdenfors–Makinson 1985 (AGM), Dong et al. 2015 (Knowledge-Based Trust), Wikidata data model, W3C PROV-O

## Adaptability: could domain modules sit on an existing system?

Read from the source on 2026-10-12 (shallow clones of each default branch; a few hours of
reading, not a build). The question: where would this design's module types (ontology,
encoder, processor, decoder, integration, knowledge pack) plug in?

| Module type | Hindsight | Cognee | Graphiti |
|---|---|---|---|
| Ontology | `entity_labels`: a controlled `key:value` vocabulary; facts are free text (`memory_units.text`, `fact_type`), no predicates | Pydantic `DataPoint` subclasses: fields become typed edges, `identity_fields` give stable ids; an RDF/OWL ontology resolver (`modules/ontology`) | Pydantic `entity_types`, `edge_types`, `edge_type_map` (allowed types per entity pair), `strict_edge_types` per `add_episode` call |
| Encoder | `retain_extraction_mode=custom` + `retain_custom_instructions` (a prompt), `retain_mission` | Tasks in `run_custom_pipeline`; any Python | `custom_extraction_instructions`; extraction prompts overridable, response schemas fixed |
| Processor | No plug-in point; consolidation into observations is built in. `retain.completed` / `consolidation.completed` webhooks (queued in the write's transaction) feed an outside sidecar | Tasks and pipelines; `memify` / `improve` for post-processing | None; code around the library |
| Decoder / recall | Built-in recall (4 channels, RRF, cross-encoder) and reflect; `MCPExtension` and `HttpExtension` add tools and endpoints | Search types and retrievers, extendable | `SearchConfig` recipes (BM25, cosine, BFS; RRF, MMR, node distance, cross-encoder) |
| Provenance and time | `document_id`, `occurred_start/end`, `mentioned_at`; quotes behind observations; no supersession | A PROV-O ledger (`modules/provenance`): source document, location, quote, agent, activity, `valid_from/until`, confidence, credibility, hash chain, `used_entities`, `invalidate()`; new (its own docstring says "PR1") | `episodes` on each edge (no span), `valid_at`/`invalid_at`/`expired_at`; contradiction decided by an LLM |
| Storage | Postgres + pgvector; extensions can ship Alembic migrations | Pluggable graph adapter (~52 async methods); Postgres graph is a "demo"; `cognee-agensgraph` is a separate package | Neo4j, FalkorDB, Neptune (Kuzu deprecated); a driver interface |
| Licence | MIT | Apache 2.0 | Apache 2.0 |

Not in any of them: beliefs computed under a declared trust policy and replayable; predicates
declared functional (or not), so contradiction can be decided without a model; staleness per
predicate; the database refusing writes without provenance; a loop guard on processors.

Reading: Cognee's extension model is closest to this design's modules; Graphiti has the best
typed ontology and temporal edges but no processor framework and no Postgres; Hindsight is a
configurable product, so modules would live in a sidecar fed by its webhooks.

## Belief-layer projects: MnemeBrain and Mneme

Read from source on 2026-10-12.

**MnemeBrain** ([mnemebrain.ai](https://mnemebrain.ai/),
[mnemebrain-lite](https://github.com/mnemebrain/mnemebrain-lite), MIT; the full "Core" is a
private repo). A belief is a free-text claim with evidence that SUPPORTS or ATTACKS it; truth
state is Belnap's four values (TRUE, FALSE, BOTH = contradiction, NEITHER) from summed evidence
weight × reliability × decay against fixed thresholds; confidence is log-odds; `retract`
invalidates evidence and recomputes; `explain` returns the justification. Kuzu storage,
FastAPI, MCP, about 2,000 lines. Limits: decay is computed against the wall clock
(`datetime.now()`), so a belief can't be recomputed as of a past time; four hard-coded belief
types carry the half-lives (fact 365 d, preference 90, inference 30, prediction 3), no ontology;
the caller decides whether evidence attacks or supports; claims are text, not subject and
predicate, so contradiction is only what the caller declares. Last commit March 2026.
Its benchmark ([BMB](https://github.com/mnemebrain/mnemebrain-benchmark), 48 tasks, no model
calls) tests its own API's concepts and skips tasks a system doesn't declare, so its own
systems score 93–100% and Mem0 29%; the adapter interface and the deterministic scenarios can
be reused, the leaderboard can't.

**Mneme** ([BrettNye/Mneme](https://github.com/BrettNye/Mneme), TypeScript, SQLite, about
24,000 lines and 2,000 tests, one author, June–August 2026, **no licence**: all rights
reserved, so nothing can be copied). Closest to this design of anything found: an append-only
claim ledger keyed by `(subject, key)`; supersession with lineage (`history`); bitemporal `asOf`;
cardinality **declared** per key (`single` = latest wins, `multi` = values coexist); Beta
confidence; derived beliefs carry their inputs and model versions and replay "exact or names
the divergence"; maintenance only **proposes** (aliases, splits, cardinality) and never applies;
extraction is a callback injected by the caller; 10 MCP tools. Its LongMemEval findings
(knowledge-update slice, retrieval-layer only): supersession serves the updated value 0.9 vs
0.1 for plain similarity on 20 questions; recency blending +5.6 points; and **the dominant
failure is key identity** — the same fact under drifting key names, so supersession and
contradiction never fire. Its `reconcile` (reuse an existing subject/key before minting one)
and census tools are its answer.

Lessons for this design: functional predicates must be declared, not guessed (both agree);
recompute beliefs as of a time, never against the wall clock; the encoder's hardest job is
landing a fact on an existing predicate and thing, so Resolve applies to predicates as well
as things, and the ontology module is what makes that possible.

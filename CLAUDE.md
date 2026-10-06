# World Model Kernel (OpenReplicant)

The substrate for an entity that senses and remembers. It observes through any channel
(endpoints, streams, watchers, bulk ingestion, conversation), interprets what it senses
into sourced **claims**, and keeps them as one persistent, interconnected world model whose
belief is computed, never asserted. Agents write claims plus the **graph operations** they
justify; the kernel validates them against ontology rules and commits the claim, the log
entry and the graph change in one Postgres transaction. The log is append-only and is the
source of truth; the graph is its projection.

Everything else builds on this core: recalling the relevant claims into an agent's context,
mapping businesses and systems, automating and governing them, and later the cognitive
model. Design: `docs/design-v1.md`; decisions: `docs/decisions/` (Phase 3 direction:
ADR 0026). Read the relevant ones before changing anything architectural.

## Direction

- **Epistemic at the core.** Every system built on this knows what it knows, who said so,
  since when and how sure, and shows its disagreements instead of resolving them. A
  history ingested in bulk (implanted memories) and one observed first-hand (lived ones)
  stay distinguishable through basis, source and origin.
- **The database thinks where it can.** Prefer deterministic logic over the persistent
  structure (belief, resolution, conformance, drift, recall ranking) to model calls.
  Models interpret what is sensed and judge what the structure cannot.
- **Harness- and model-agnostic.** The gateway is an MCP server any harness can use; the
  extractor runs on any configured model endpoint; adapters talk to any runtime. Use
  existing MCP servers at the edges (parsers, paper sources, forges, clusters, process
  mining) and keep the kernel small.
- **The north star** is a cognitive model that improves itself by reading new research
  (the research pack over the daily arXiv stream) and testing techniques against its own
  evals. It comes after Phase 3; nothing here builds it yet.

## Built so far (Phases 1 and 2, complete)

Keep all of it working; its invariants and tests still apply.

- Kernel 0.6.1 (`kernel/sql/`): the log and seven operations, quotes checked against their
  source, extraction runs, belief v2 counting independent origins, the two clocks and
  as-of reads, the resolution cascade, sealing and erasure of personal data (ADR 0022),
  and governance: proposals are data, only a signed-in person decides (ADR 0029).
- Gateway (`gateway/`): `write`, `write_batch`, `lookup_entities`, `get_schema_slice`,
  `query_graph`, `query_log`, `ingest_source`, `cite`; RFC 9457 problems; OTel; profiles.
- Skills: `skills/core`, `skills/interview`. Packs (`packs/`): `research` (papers, the
  papers server with GROBID full text), `software` (repository adapter, the self-model),
  `bpm-reference` (the kernel's test pack).
- `ui/`, the read-only explorer. Evals: fixtures, resolution set, replay, live runs, and
  the independently annotated SciFact eval (`evals/annotated/RESULTS.md`).

## Current phase: Phase 3, the closed loop (approved 2026-10-06)

One synthetic company (the `northwind` scenario) taken around the whole loop, as an open
demo: map it, choose what to automate, automate it, govern what runs, and let the system
propose changes to what it deployed, approved by a person. Each item starts with a short
ADR, then code. In this order:

1. **A general process pack** (ADR 0027, built: `packs/process`). A runtime-neutral
   representation, not BPMN: processes made of steps (`activity` and `gateway` entities)
   joined by `flows_to` edges whose `props.when` is the branch condition, with roles,
   actors, systems, data, triggers, outcomes and KPIs. It must translate to
   and from runtime configurations. Three views of how work happens, each a source:
   - **as told**, from interviews (interview skill);
   - **as written**, from SOPs and documents (parsers at the edge);
   - **as done**, from system exports and event logs (CSV, XES, OCEL 2.0), read by the
     pack's deterministic adapter (`wmk-process discover`, then `conform` against the
     mapped process). pm4py is AGPL: never imported, at most a separate service an
     operator installs (ADR 0012).

   Where the views disagree, belief shows it as contested: that is the discovery
   deliverable. `bpm-reference` stays as the kernel's test pack.
2. **Sysops and self** (ADR 0028, built for Docker in the software pack). Extends the
   software pack from what a repository declares to what runs: deployments, versions,
   health and changes, read by deterministic observation adapters (`wmk-software capture`,
   `observe`, `drift`); Kubernetes, Grafana or Prometheus and the forge are later readers of
   the same model. Drift is the declared view contradicting the observed one, shown as
   contested; `make observe-self` observes the kernel's own stack.
3. **The approval channel (ADR 0019, built by ADR 0029).** People approve, the actuator
   enforces, the kernel records. The kernel part is built: `kernel.decide`, run only as
   `kernel_approver` for the person a verified token names, and governance rules in
   `kernel.write` (no machine decides, no self-approval, approvers outside the system they
   change, instruments need two people). Approval adapters per channel:
   - the explorer's signed-in Proposals page for decisions that are not code (activating
     an automation, an erasure, a policy): built with a static one-person token (ADR
     0030); sign-in through an OpenID Connect provider (Forgejo) is next;
   - a forge for code and configuration: GitHub, or a self-hosted Forgejo or GitLab, with
     branch protection and required reviews. Its adapter is built for GitHub (ADR 0031,
     `wmk-software forge`): it records pull requests, reviews and merges as evidence of
     decisions, never as decisions, and audits which changes, and which deployments of them,
     no person approved.
4. **Workflow adapters, one per runtime.** The first is Operaton (chosen 2026-10-06: the
   Apache-2.0 fork of Camunda 7, BPMN with a REST API and run history); later ones may be
   n8n, RuleGo, any rules engine, runner or plain code. Each compiles the process representation into its runtime's
   configuration, and reads deployments and runs back as observed claims and events, so
   conformance compares what ran with what was mapped. The representation never depends on
   one runtime.
5. **The demo and its write-up.** `make demo` runs the loop on Northwind end to end, with
   the extractor (item below) and scripted fallbacks so CI can run it; a short video and a
   write-up with honest numbers.

Also in scope:

- **The extractor** (ADR 0024, revised): a CLI for bulk ingestion and an MCP server that
  takes `map_source` work from any harness, on configured model endpoints (the Anthropic
  API, or an OpenAI-compatible server for local models). It moves documents from parser
  servers to the gateway with no model in between; models only write claims. Built first
  as an eval harness (comparing models, the raw-text control); bulk use waits for the
  annotated eval's floors.
- **Research eval improvements:** a dev/test split, the raw-abstract control, structured
  findings (population, measure, direction) and `directness` on relations, a second
  reader, a cross-paper eval.
- **Recall into context** (the read side of context engineering): a proposed ADR first, no
  code until reviewed. The facts relevant to a task, with belief, contested sides, windows,
  quotes and the read offset, delivered through MCP resources, harness hooks or the
  extractor's loop; retrieval traces record what an agent was shown.

**Out of scope for now** (do not build, do not stub): a pack registry or fetching packs by
URL, pack SQL, automatic capture of agents' own sessions (observer runs that write from
what they watch, beyond deterministic observation adapters), vital signs beyond the demo's
health and drift checks, generative ops strategies beyond proposing a pull request,
concept formation, habit formation, the cognitive model itself. If a task seems to need
one of these, stop and ask.

## Open and private

This repository is public. It holds the kernel, the gateway, the skills, the general packs
(process, software with its observation of what runs, research), the demo, the evals and
their results. Client data, packs and adapters refined on engagements, enterprise
adapters, playbooks, pricing and labelled client datasets live in private repositories that
install packs from local folders. The kernel and this repository's CI never depend on or
reference them. The licence is the owner's decision: do not add or change one.

## Stack

- Python 3.12, dependencies managed with `uv`
- Official MCP Python SDK; `psycopg` 3 for Postgres
- Postgres 18 with Apache AGE, pgvector, pg_trgm, pgcrypto
- OpenTelemetry Python SDK
- `pytest` for tests, `ruff` for lint and format

## Commands

Keep these working; add `make demo` in Phase 3.

- `make up` / `make down` — start the stack / stop it and remove volumes
- `make test` — unit, SQL and regression tests
- `make replay` — rebuild the graph from the log and diff against the live graph
- `make eval` — the fixtures through the eval profile, then the resolution set
- `make lint` — ruff check and format check
- `make up-ui`, `make ui-smoke` — the explorer and its smoke check
- `make seed`, `make map-self` — the fixtures, and this repository's self-model, into a stack
- `make observe-self` — capture the running stack, observe it and check it for drift
- `make forge-self` — capture this repository's pull requests, map them and audit its
  changes for approval (`FORGE_CAPTURE=` replays a recorded capture, as CI does)
- `make token EMAIL=…` — mint a sign-in token for one person (operators only; needs
  `WMK_JWT_SECRET`, which agents never hold)
- `make live`, `make annotated` — real-model runs (cost model usage; not CI)
- `make erase-scope SUBJECT=…`, `make erase SUBJECT=… REQUESTED_BY=… APPROVED_BY=… YES=1` —
  review and carry out an approved erasure (operators only)

## Invariants — never violate these

1. **One write path.** Only `kernel.write`, `kernel.ingest_source` and `kernel.cite`
   change data, plus `kernel.erase`, which destroys data keys and is run only by an operator
   as `kernel_eraser`, never by an agent (ADR 0022). `kernel.decide` writes a person's
   decision through `kernel.write` (ADR 0029). Gateway code never issues INSERT,
   UPDATE or DELETE directly. Bulk loading and every adapter are many payloads through the
   gateway, never a direct import.
2. **The log is append-only.** No UPDATE or DELETE on log tables, enforced by
   permissions and a trigger. There is no delete operation; retraction is a new
   assertion with opposite polarity, or a `supersedes`. The only rows ever deleted are data
   keys, by `kernel.erase`; erasure never touches the log.
3. **Projection is deterministic.** Triggers and projection functions make no network
   calls, never call `now()` (use the entry's `recorded_at`), use no randomness; they may
   unseal, never seal. Replaying the log with the data keys that remain must reproduce the
   graph exactly.
4. **The log stores operations in kernel vocabulary.** Never Cypher, never SQL, never
   an instruction to call a model.
5. **No model calls inside the database or inside a transaction.**
6. **Belief is a pure function of assertions.** Each source counts once, through its latest
   assertion, and each origin (who a source comes from) counts once (ADR 0021). No time decay.
   Conflicts follow the kernel's policy: same source newer supersedes; a different
   source makes the fact contested. Never resolve a conflict by overwriting.
7. **Kernel node types and edges are fixed.** Four node types (Entity, Agent, Claim,
   Event) and the kernel edge list in the design doc. Packs specialize; they never add
   kernel-level types or edges.
8. **Rejections are RFC 9457 problem documents** naming the broken rule, plus duplicate
   candidates or nearest allowed kinds where relevant.
9. **No personal data in telemetry.** Spans and app logs carry IDs, never claim text,
   source content or message content.
10. **The kernel is passive; actuation is gated.** The kernel and the gateway record and
    answer; they never act on the outside world. Adapters that change a runtime, a
    repository or a system live in packs, act only on a change a person approved through
    the approval channel (ADR 0019), and hold no credential that bypasses it. Agents never
    approve: `kernel.write` refuses any decision on a proposal not made by a signed-in
    person through `kernel.decide` (ADR 0029).

## The write payload

```json
{"claim": {"text": "...", "source": "<chunk id>", "quote": "<the chunk's words>",
           "basis": "reported", "modality": "descriptive", "run": "<extraction run>"?},
 "read_at_offset": 48210, "trace_id": "...",
 "ops": [{"op": "assert", "edge": "implements", "from": "agt_sam",
          "to": "role_approver", "valid_from": "2026-03-01"}]}
```

Operations: `create`, `assert`, `link`, `unlink`, `promote`, `transition`, `redact`. A
payload may also send `close_run`, which `kernel.write` resolves into negative assertions
and a transition before logging (ADR 0020): the log only ever holds the seven.

A reported claim quotes the words of its chunk it rests on; the kernel finds them in the
source and records their span, or refuses the claim. Extraction of a whole source happens
in a run (an Event of kind `extraction`); closing it retracts what older runs over the
same source found and it did not. Sources about people list them as `subjects` and are
stored sealed, so erasing a person makes them unreadable (ADR 0022).

`kernel.write` steps, in order: take the append lock and assign the next offset →
check provenance (source, quote, run) → validate every op against ontology rules → reject
if any touched node changed after `read_at_offset` → run the resolution cascade on every
`create` → seal personal data → append the log entry and apply graph changes → update
belief.

A claim that maps to no ontology term is stored as an **unresolved claim** (text and
provenance, no ops). Never drop it.

## Conventions

- **SQL:** kernel objects live in schema `kernel`; files in `kernel/sql/` are numbered
  and applied in order. Every function has a comment stating its contract.
- **Python:** fully typed; small modules; no ORM.
- **Errors:** build problem documents in one place (`gateway/problems.py`).
- **OTel:** all span and attribute names live in `gateway/otel.py`. The GenAI and MCP
  semantic conventions are not yet stable; never scatter names through the code.
- **IDs:** ULID or UUIDv7, assigned when the entry is logged, never by the graph.
- **Two clocks:** `valid_from`/`valid_to` (true in the world) and `recorded_at` plus
  offset (when learned). Keep them separate in every query and test.
- **Decisions:** anything that changes the design gets a short ADR in `docs/decisions/`.
- **Packs:** self-contained folders (`docs/packs.md`): ontology in `schema.yaml` and
  `rules.yaml`, never SQL; their tests, fixtures, live scenarios, servers and adapters
  inside the folder; servers and adapters are uv workspace members. The kernel never
  depends on a pack; its own tests use only `bpm-reference`.
- **Adapters:** deterministic wherever the input is structured (configs, exports, event
  logs, runtime APIs), writing observed claims that cite what they read; one adapter per
  system or runtime; they reach the kernel only through the gateway, never with database
  credentials. Prefer an existing MCP server or library at the edge over writing a client.
- **Models:** never in the kernel or the gateway. The extractor and the harnesses call
  them, on configured endpoints; skills stay plain Markdown that any harness can load.

## Testing

- Every invariant above has at least one test.
- `make replay` runs in CI on every change; a non-empty diff fails the build.
- Regression tests for known failure modes from Graphiti's history:
  - invalidation must stay scoped to the same edge type and endpoints;
  - non-overlapping validity windows must never collapse into one edge;
  - bulk writes must pass the same rule checks as single writes.
- Evals compare the produced graph with the expected graph per fixture. Report
  precision and recall for entities and edges separately.
- Fixture evals measure the plumbing; extraction quality is measured only against data we
  did not annotate ourselves (`make annotated`), with intervals, never against fixtures.
  Tune on a dev split and report on a held-out test split.
- Adapters are tested on recorded inputs, so CI needs no network, cluster or runtime.
- The demo runs in CI in its scripted form.

## Glossary

- **Claim:** a sourced statement; becomes the assertion record for its ops.
- **Assertion:** source, agent, time, confidence, polarity, modality and basis backing
  an edge or claim.
- **Basis:** observed, reported or inferred.
- **Modality:** descriptive, predictive, normative, proposed or hypothetical.
- **Belief status:** accepted, contested, rejected or unknown.
- **Unresolved claim:** a claim kept without graph ops until it can be placed.
- **Origin:** who a source's content comes from (authors, a speaker, a publisher, a
  repository); belief counts each once, however many sources repeat it.
- **Data subject:** a person the kernel holds data about, named by their Agent id; a source
  lists its subjects, and a human agent is its own.
- **Sealed:** stored encrypted under a data key so that destroying the key erases it.
- **Schema slice:** the kinds, edges and rules most relevant to one passage.
- **Step, condition:** a process's unit of work (an `activity`) and the condition on the
  `flows_to` edge to the next step.
- **Three views:** how work happens as told, as written and as done; their disagreements
  are contested facts.
- **Observation adapter:** deterministic code that reads a system and writes what it sees
  as observed claims.
- **Workflow adapter:** code that compiles the process representation into one runtime's
  configuration and reads its deployments and runs back.
- **Drift:** the declared view of a system contradicted by the observed one.
- **Conformance:** how far what ran matches what was mapped.
- **Proposal, approval:** a claim of modality `proposed` describing a change in
  `props.change`, stating no facts; a decision on it by an authenticated person who did not
  propose it, through `kernel.decide` (ADR 0019, 0029).
- **Instrument:** what judges changes (fixtures, CI, rules, the approval rules); a node a
  protecting claim is about. Proposals about one need two people and stand alone.

## When unsure

Check the design doc and the ADRs. Prefer the smallest change that satisfies the
invariants. Do not expand scope beyond the current phase without asking.

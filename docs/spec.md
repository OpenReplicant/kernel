# Knowledge Kernel — specification v0

Status: **final for milestone 1**. Kernel v0 (§3) is frozen: changing it needs evidence from the
evaluator and an explicit decision by the project owner, recorded in `docs/decisions.md`.
Everything else is a module or a vocabulary pack and may change freely through the normal process.

"Knowledge Kernel" is a working name (decisions O-1).

---

## 1. What this is

A general-purpose engine that **learns from claims and acts on them**. It ingests sources through
encoders, keeps everything it knows as attributed, referenced, belief-scored claims in one
Postgres database, models systems (software, businesses, processes, and itself) with standard
vocabularies, and turns those models into running things through decoders and actuators. It
watches what happens and adapts through the same governed write path.

Almost every component already exists. What this project contributes is the **configuration**: a
small kernel that enforces epistemic guarantees in the database, a stable set of slot contracts that
modules plug into, and a default distribution of modules and vocabularies.

```
 sources ─encoders─▶ change sets ─▶ ┌────────── kernel (Postgres) ──────────┐ ─▶ read views / MCP / UI
 people  ─interviews▶              │ entity · claim · artifact · run ·      │
 runtimes─observers─▶              │ change set   + invariants + RLS       │ ─decoders─▶ artifacts
                                   └────────────────────────────────────────┘        │
                    processors (validate, belief, verify, reason) ◀── notify          ▼
                                                                         actuators ─▶ runtimes
```

## 2. Three layers

| Layer | What it is | Changes how |
| --- | --- | --- |
| **Kernel** | Five primitives, six invariants, the apply function, row-level security. Plain Postgres 16+, no extensions. `kernel/schema/` | Frozen at v0. Change needs evaluator evidence + owner decision |
| **ABI** (slot contracts) | Change-set format, module manifest, chain profile, slot catalog, hook contract. `kernel/schemas/`, `ontology/slots.yaml`, `kernel/contracts.py` | Versioned (semver). Additive changes are minor; breaking changes are major and need a decision |
| **Distribution** | Default vocabulary packs and modules: API (PostgREST), UI (Caddy + Alpine.js), MCP, validator, belief model, encoders, decoders, runners. `ontology/`, `modules/`, `deploy/` | Normal module lifecycle (review lane + eval) |

Operating-system reading of the same thing: Postgres is the kernel (state, permissions, IPC via
NOTIFY), PostgREST is the system-call interface (views + `submit_change_set`), each module runs under
its own database role (its uid), modules are processes in containers, encoders and actuators are
drivers, and agents and the UI are user space.

Two properties make this more than a configured Postgres:

1. **It describes itself in its own model.** Modules, slots, chains, vocabularies, runs and eval
   results are entities and claims, so the system can reason about itself and propose changes to
   itself through the same governed path as everything else.
2. **Evidence picks what fills a slot.** When two modules compete for a slot (two belief models, two
   chain runners), the evaluator scores them on the same eval set and records the scores as claims.
   Nothing is trusted because it exists; it is trusted because it was measured.

---

## 3. Kernel v0

Source: `kernel/schema/001_kernel.sql` (schema, rules, RLS, API), `002_bootstrap.sql` (prefixes,
settings, default scopes, core W3C properties). Tests: `kernel/schema/tests/` (30 checks covering
every invariant below; `sh kernel/schema/tests/run.sh`).

### 3.1 Five primitives

| Primitive | Table | What it is |
| --- | --- | --- |
| **Entity** | `kernel.entity` | An identifier (IRI). Meaning comes only from claims. Any agent may mint one |
| **Claim** | `kernel.claim` (+ `claim_reference`, `claim_derivation`, `claim_status`, `belief`, `verification`) | One statement: subject, predicate, object (entity or JSON-LD value), qualifiers, valid time, recorded time, scope, evidence type, attribution, run, change set, statement key |
| **Artifact** | `kernel.artifact` | Immutable, content-addressed bytes (sha256) held by the artifact store: sources, generated files, reports |
| **Run** | `kernel.run` | One execution by an agent (PROV activity): module, plan step, what it used, cost |
| **Change set** | `kernel.change_set` | The only way anything is written: a batch of ops, applied atomically or not at all |

Everything else is entities + claims defined by vocabulary packs: terms, mappings, modules, slots,
chains, agent profiles, eval results, adaptation records.

Supporting tables: `agent` (agent IRI ↔ database role), `scope` and `scope_grant` (clearance),
`prefix` (CURIE expansion), `setting` (active belief model, review rules), `redaction`.

**Claims are bitemporal.** `valid_from`/`valid_to` say when the statement is true in the world;
`recorded_at` says when the kernel learned it. Status and belief are append-only histories, so "what
did we believe on date X" is a query, not a reconstruction.

**Statement key.** A hash of subject, predicate, object and qualifiers. Claims from different
sources with the same key are the same statement and corroborate each other (belief model input).

### 3.2 Invariants

| # | Invariant | Enforced by |
| --- | --- | --- |
| 1 | Every claim is attributed (agent + run) and referenced (a source or `derived_from` claims). Extracted claims carry the supporting quote | NOT NULL columns; apply checks `KK032`, `KK033`; attribution must match the submitting role `KK010`, `KK011`; SHACL `kk:ClaimShape` on export |
| 2 | Nothing is deleted. Claims are superseded, refuted or retracted by status, keeping history. The only content removal is **redaction**, which keeps identity, attribution and lineage and is itself a recorded run. Belief always comes from a recorded model | Immutability trigger `KK001`; privileges; `kernel.redaction` (stewards only, `KK060`) |
| 3 | A claim's scope is at least as restrictive as everything it was made from: input claims and referenced artifacts | Apply checks `KK035`, `KK037` (readers of the target scope ⊆ readers of each input's scope) |
| 4 | Clearance is granted by people and enforced by Postgres, never by a model's judgment | `scope_grant`, roles, RLS on every claim table; write clearance check `KK012` |
| 5 | No method verifies its own output | Validator ≠ submitter `KK023`; reviewer ≠ submitter `KK025`; verifier module and agent ≠ the claim's producer `KK050` |
| 6 | Every answer cites claim ids and shows their belief | Not enforceable in the database: the kernel MCP skill, the harness stop hook and the eval gate (§13) |

Kernel rules that support them:

- **Approved predicates only.** A predicate must be typed as a property by a non-deprecated claim
  (`KK030`, `KK031`). Typing a new property, class, module, chain, agent profile or TOSCA node type, and
  any `rdfs:subClassOf`/`subPropertyOf`/`domain`/`range`/`owl:equivalent*`/`skos:*Match` claim, is
  **forced into the review lane** (settings `review_predicates`, `review_types`).
- **Optimistic concurrency.** `set_status` may carry `expect_status`; a mismatch marks the change set
  `conflict` (`KK409`) instead of overwriting.
- **Idempotency.** `idempotency_key` is unique; resubmitting returns the existing change set.
- **One scope per change set.** Status, belief and verification ops address claims in the change
  set's own target scope (`KK040`).
- **Migrations write through the same path.** Vocabulary packs and module installs run as `kk_owner`
  and use `kernel.install_ops`, so even installed vocabulary is attributed, referenced and recorded.
  Owner installs skip the in-app review lane because migrations are reviewed as code (D-19).

### 3.3 Write path

```
agent ──submit_change_set──▶ submitted ──validator──▶ valid ──(auto lane)──▶ apply ──▶ applied
                                  │                     │                      ├──▶ invalid  (report says why)
                                  │                     └─(review lane)─▶ awaiting_review ──reviewer──▶ approved ──▶ apply
                                  └──validator──▶ invalid                                  └──▶ rejected        └──▶ conflict
```

- `api.submit_change_set(cs)` runs as the caller; triggers force `submitted_by = current_user` and check
  attribution, run ownership, write clearance and the lane.
- The **validator** (a module in role `kk_validator`) checks the JSON Schema and SHACL shapes, then
  calls `api.record_validation`. It must be cleared to read the change set's scope.
- `kernel.apply_change_set` is `SECURITY DEFINER`, owned by `kk_owner`, callable only from the
  status-change trigger. It applies all ops in one subtransaction; any data error rolls the ops back
  and marks the change set `invalid` (or `conflict`) with the error in `validation_report`.
- Every insert and every apply sends `NOTIFY kernel_change_set` with `{id, status, scope, lane}`.

### 3.4 Read path

PostgREST exposes only schema `api`:

| Endpoint | What |
| --- | --- |
| `GET /claims` | All visible claims with current `status` and the active model's latest `belief` |
| `GET /change_sets` | Your own change sets (validators/reviewers: all in their cleared scopes) |
| `GET /review_queue` | Reviewers only |
| `GET /rpc/explain?claim_id=` | Sources and quotes, derived-from, used-by, status history, beliefs, verifications, run |
| `POST /rpc/start_run`, `/rpc/finish_run`, `/rpc/submit_change_set` | All agents |
| `POST /rpc/record_validation` / `/rpc/review` / `/rpc/redact` | Validators / reviewers / stewards |
| `GET /validation_queue` | Validators: submitted change sets with their ops (view added by the validator module's migration, D-24) |
| `GET /claim_references` | References of the claims you can see (view added by the belief module's migration, D-27) |

Module migrations may add `security_invoker` views to `api`; kernel row-level security still applies.

Row-level security filters every claim table by the caller's scope clearance; dependent rows
(references, derivations, status, belief, verification) are visible exactly when their claim is.

Fast reads for chat and real time come from **read models**: materialized views, entity cards and
caches maintained by read-model modules, refreshed on `kernel_change_set` notifications. Read models
must apply the same scope filter (materialized views have no RLS, so each is built per scope).

### 3.5 Error codes

| Code | Meaning | Code | Meaning |
| --- | --- | --- | --- |
| KK001 | Append-only table modified | KK030 | Predicate is not a known term |
| KK002 | Role not mapped to an agent | KK031 | Predicate not approved |
| KK003 | Run not yours / not running | KK032 | Claim has no reference or derivation |
| KK010 | Attribution doesn't match role | KK033 | Extracted claim without quote |
| KK011 | Run belongs to another agent | KK034 | Unknown artifact |
| KK012 | No write clearance for scope | KK035 | Artifact in a more restrictive scope |
| KK020 | Change-set content edited | KK036 | `derived_from` claim not found |
| KK021 | Status transition not allowed | KK037 | Derived claim would leak to a wider scope |
| KK022 | Only validators validate | KK039 | Unknown op |
| KK023 | Self-validation | KK040 | Claim not in the change set's scope |
| KK024 | Only reviewers decide | KK041 | Deprecation without a reason |
| KK025 | Self-approval | KK050 | Self-verification |
| KK060 | Only stewards redact | KK070/071 | `install_ops` misuse / failure |
| KK409 | Stale `expect_status` (conflict) | | |

### 3.6 Outside the kernel on purpose

The kernel does not: compute belief, run SHACL, schedule anything, call models, store bytes, or
need any extension. Those are modules. If a feature seems to belong "in the core", it almost always
belongs in a module that reads views and submits change sets.

---

## 4. Identity, clearance, trust and budgets

- **Agents are roles.** Every agent (module, person, external system that logs in) is a database
  role mapped one-to-one to an agent IRI in `kernel.agent`. Modules use roles named `kkm_*`, members of
  `kk_agent` (plus `kk_validator`, `kk_reviewer` or `kk_steward` where needed). People use `person_*` roles
  (the local owner, `person_owner`, reviews and may redact; D-26) and harness agents that work through MCP
  use `agent_*` roles (`agent_claude_code`; D-28). PostgREST connects as
  `kk_authenticator` and switches to the role in the caller's JWT (`scripts/mint_jwt.py`). Each role is
  granted to `kk_authenticator`.
- **Scopes are trust boundaries.** `kernel.scope_grant` says which roles may read and write each
  scope. Defaults: `urn:kk:scope:vocab` and `urn:kk:scope:default`, readable and writable by `kk_agent`.
  Validators and reviewers see change sets only for scopes they can read.
- **Three kinds of trust, never mixed:**
  - *Clearance* — what an agent may read and write. Granted by people; enforced by Postgres.
  - *Belief* — how much the system believes a claim. Computed by the active belief model; shown with
    every answer; drives preferred/deprecated status through processors.
  - *Reliability* — an agent's or source's track record (eval scores, refutation rate). Feeds belief,
    never clearance.
- **Budgets.** Every run records its cost (`run.cost`: tokens, money, CPU). Module manifests declare
  budgets; enforcement is done by the model-provider and harness modules against those budgets, and
  overruns are observed claims. The kernel records; it doesn't throttle (v0).
- **Evidence ordering** used by belief models and verifiers: deterministic (code, schemas, config,
  traces) > extracted (docs) > inferred; observation of the running system outranks all; testimony is
  attributed to the person and weighed by their reliability.

---

## 5. The ABI: slot contracts

### 5.1 Module kinds

| Kind | Contract | Writes claims? |
| --- | --- | --- |
| encoder | source → change sets | yes |
| processor | claims or events → change sets | yes |
| decoder | shaped claims (+ mappings) → artifact in a target standard, plus a list of what it couldn't represent | only metadata about its output |
| actuator | artifact → effect in an external runtime; fixed action list | **never**; observations come back through an encoder |
| interface | exposes the kernel (API, MCP, UI, events, identity) | no |
| agent | harnesses, profiles, skills, harness adapters | through their agents' change sets |
| control | runners, schedulers, evaluator, module manager | yes (runs, eval scores) |
| pack | vocabulary content | via migrations |

Python shapes of these contracts: `kernel/contracts.py`. Wire formats (all languages):
`kernel/schemas/change_set.schema.json`, `module.schema.json`, `chain.schema.json`.

### 5.2 Module manifest

`modules/<name>/module.yaml` (schema `kernel/schemas/module.schema.json`). Declares identity
(agent IRI + `kkm_*` role), the slots it fills, requested clearance (granted by a person at install),
budget, runtime (`rulego-chain`, `python-service`, `container`, `sql`, `skill-only`), requirements
(TOSCA-style), accepts/produces, triggers, interfaces (WoT-style affordances with transports), MCP
exposure allow-list, migration, skill and eval paths, and `protected`.

**Protected** modules (validator, belief model, runners, decoders that deploy chains, ingest filter,
evaluator, pack loader, kernel MCP) are system components: never exposed for editing; changes need
the review lane and a passing eval.

Installing or changing a module: the module manager submits the manifest as claims (review lane),
a person approves and grants clearance, the migration runs as `kk_owner`, and the orchestrator driver
starts it. See `modules/_template/`.

### 5.3 Slot catalog

`ontology/slots.yaml` — 42 slots in seven groups (input, knowledge, decision, agent, output,
interfaces, control), each with a contract, the expected kind, cardinality, first default and
milestone. **Single-active** slots (cardinality `one`: belief model, shape validator, chain runner,
clearance policy, identity, API adapter, entity resolver, router, context assembler, evaluator,
module manager, artifact store, secret store, event transport, orchestrator driver, curriculum,
self-telemetry) run alternatives in **shadow mode** until the evaluator promotes one. The rest take
any number of modules.

Deliberately not slots: a publisher is a decoder + actuator pair; a notifier is an actuator; the
answer composer is a harness running a profile over read models; the MAPE-K manager is a chain.

### 5.4 Hook contract

Kernel hook events: `session_start`, `before_tool`, `after_tool`, `before_compact`, `stop`. Hooks are
**harness-specific**: a harness adapter maps these onto the harness's own mechanism (or skips what it
doesn't support). Hooks are ergonomics — injecting entity cards, nudging citations, catching mistakes
early. **Guarantees never depend on hooks**: clearance, attribution and the write path are enforced
at the MCP/API boundary and in Postgres, so an agent in a harness without hooks is less comfortable
but exactly as safe.

---

## 6. Ontology architecture

**Rule: one concept, one home.** Every vocabulary sits in exactly one tier (`ontology/packs.yaml`):

- **core** — owns a concept nothing else owns; always installed;
- **profile** — loaded only for a domain or capability;
- **format** — an encoder source or decoder target, reached through mappings, never mixed into core terms.

A new vocabulary enters only with an entry naming the concept it owns. If something already owns
that concept, the newcomer is a format or is rejected.

| Concept | Home | Verdict on competitors |
| --- | --- | --- |
| Identity, types, labels | RDFS, SKOS, DCTerms | Wikidata P-ids are link targets only (`skos:exactMatch`) |
| Provenance | PROV-O | wfprov via mappings |
| Plans and steps | P-Plan | TOSCA workflows unused; BPMN is a profile; wfdesc via mappings |
| Quotes and spans | Web Annotation (`oa:`) | — |
| System structure (components, slots, fulfillment, dependencies, sub-topologies) | TOSCA 2.0 metamodel mirror `kk-tosca.ttl` | Replaces the earlier hand-made software properties; SOSA/SSN only in an IoT profile |
| Runtime interfaces and transports | WoT Thing Description | TOSCA interfaces only for lifecycle operations; OpenAPI, AsyncAPI, MCP schemas, A2A cards are formats |
| Constraints | SHACL | ShEx unused; UVL deferred |
| Mappings | SSSOM semantics stored as `skos:*Match` claims; confidence feeds belief | — |
| Claims, scope, evidence, status, belief, verification, modules, slots, chains, eval | `kk-core.ttl` — the only invented terms | — |
| Agent profiles and skills | `kk-profile.ttl` (profile) | A2A Agent Card, Agent Skills, harness files are formats |

TOSCA is YAML-native with no official RDF vocabulary, so `kk-tosca.ttl` mirrors its metamodel one to
one (about fifteen classes and properties) and nothing more.

**Storage vs meaning vs graph.** Storage is a SQL claims log (what you maintain). Meaning is RDF:
global IRIs and RDF-native vocabularies; export is N-Quads with scopes as named graphs, claims as
statement nodes (upgradeable to RDF 1.2 reifiers). Ergonomics are property-graph-like: every claim has
its own id and qualifiers, which act as edge properties. The **graph is a projection**, built only where
graph algorithms pay off: recursive CTEs for traversal, Apache AGE inside the same Postgres if openCypher
is wanted, or a NetworkX/igraph projection for analytics.

**Namespaces.** `kk:` = `https://w3id.org/knowledge-kernel/ns#`, `kkt:` = `…/tosca#`, `kkp:` =
`…/profile#` are placeholders until a w3id.org redirect is registered (O-2). Instance IRIs use
`urn:kk:` (`urn:kk:module:…`, `urn:kk:agent:…`, `urn:kk:scope:…`, `urn:kk:chain:…`).

---

## 7. System-modeling profile and the compile rule

The same profile models any system — and first of all this one.

| Thing | Modeled as |
| --- | --- |
| A kind of component (a module) | `kk:Module` ⊂ `kkt:NodeType`, with `kk:moduleKind`, `kk:version`, `kk:runsAs`, `dcterms:license` |
| A slot | `kk:Slot` ⊂ `kkt:RequirementDefinition`, with `kk:slotContract`, `kk:cardinality` |
| Filling a slot | `kk:fills` ⊂ `kkt:fulfills`; configuration chosen for the fulfillment goes in qualifiers |
| A running instance (a compose service) | `kkt:NodeTemplate` with `kkt:nodeType`, `kkt:propertyValue` (qualifier `kkt:propertyName`), `kkt:memberOf` the distribution's `kkt:ServiceTemplate` |
| Dependencies | `kkt:dependsOn` / `kkt:hostedOn` / `kkt:connectsTo` between templates |
| Runtime interfaces | `kkt:hasAffordance` → `td:` actions, properties, events, each with forms (transport bindings) |
| A chain | `kk:Chain` ⊂ `pplan:Plan`; steps `kk:ChainStep` ⊂ `pplan:Step` with `kk:stepType`, `kk:stepConfig`; routing `kk:next` with qualifier `kk:onLabel` |
| A sub-chain or sub-topology | `kkt:SubstitutionMapping` (`kkt:substitutes` a node type, `kkt:substitutedBy` a service template) |
| A run of a step | `kernel.run.plan_step` = the step IRI (`p-plan:correspondsToStep`) |

**Compile rule.** A fulfillment is *runnable* when (a) its node type maps to a component the target
runner supports (a RuleGo node type, a Python step), (b) all its requirements are fulfilled, and (c)
its transports are supported by the runner. Then a decoder compiles the topology into the runner's
format: fulfillments become nodes, relationships become connections, substitution mappings become
sub-chains. Anything that fails stays *described, not runnable*: the decoder records `kk:runnable
false` with `kk:notRunnableBecause`, and that gap becomes a curriculum item.

**The distribution is a service template.** Installing it means decoding that template into Compose,
Podman or Kubernetes manifests, so the module manager and orchestrator drivers are just another
decoder-and-actuator pair.

---

## 8. Chains and runners

- **Chain profile.** Short, event-driven automations: a trigger and a DAG of steps (`http`, `script`
  in Python/JS/Lua, `subchain`, `switch`, `filter`, `transform`, `mcp_tool`, `log`) with routing labels
  (`success`, `failure`, `true`, `false`, switch labels). Modules ship them as YAML
  (`kernel/schemas/chain.schema.json`); the self-encoder turns them into claims; a chain decoder compiles
  claims into a runner's format. Long-running processes with human tasks use BPMN (milestone 2).
- **Runners: existing ones, reached through decoders** (chain-runner slot, single-active; D-31). The
  chain claims are the runner-neutral source; a decoder per runner compiles them and records what it
  could not represent. We do not maintain an interpreter of our own (a Python stand-in carried
  milestone 1 up to T10 and is retired).
  - *RuleGo-Server* (Apache-2.0, v0.38.0 built with `with_extend`) — the runner: hot-reloaded
    file-based chains, JWT auth, JavaScript and Lua nodes, an `x/python` node (inline script or `.py`
    file) and an `exec` node with a command whitelist, sub-chains and `for` loops, an MCP mode, an
    OpenTelemetry client node. Decoder: `modules/chain-decoder-rulego` (D-30, D-33).
  - Further runners are further decoder targets (the compile rule in §7 is per runner).
- **Protection, in three layers.**
  1. The invariants never live in a runner. Chains reach the kernel only through PostgREST under their
     `runs_as` agent's JWT, so an edited or broken chain can only submit change sets.
  2. System chains are protected: defined as claims, changed only through the review lane, decoded
     into the runner's chain folder by the chain decoder (the only writer). Agents never get RuleGo's
     editor or editing API. The decoder records each deployed file's hash (`kk:deployedHash`); a drift
     check compares deployed files with approved chains.
  3. MCP exposure is an allow-list of approved chains; raw `cmd`, HTTP and component tools are excluded.
- **Runs.** Every step that writes to the kernel gets its own kernel run with `plan_step` = the step
  IRI (`<chain IRI>/<step id>`), started before the call and finished after it; read-only steps and
  executions that find nothing to do record no runs (D-25, D-31). Every decoder compiles to this.
- **Identity.** RuleGo-Server's MCP mode does not pass the caller's identity into a chain (T1, D-21),
  so agent-facing tools live in `kernel-mcp`, which forwards each caller's token, and RuleGo runs
  system chains under their modules' identities.
- **Triggers** (D-32). `notify`: a NOTIFY bridge (event-transport slot) listens on `kernel_change_set`
  and calls the runner with `{id, status, scope, lane}`; the chain reads what it needs through the API
  under its own token. `schedule`: periodic work only (e.g. the drift check). `http`: the runner's
  HTTP endpoint. `chain`: called as a sub-chain.

---

## 9. Agents

- Agents work through MCP. The kernel's own tools come from `kernel-mcp` (MCP Python SDK v2,
  implementing the 2026-07-28 spec), which holds no database role and forwards each caller's bearer
  token. Agent instructions for those tools: `modules/kernel-mcp/SKILL.md`.
- Default and optional MCP servers, and the ones deliberately excluded (generic SQL servers, the
  Memory reference server): `docs/mcp-servers.md`. MCP servers are a convenience layer, never the
  enforcement layer.
- **Agent profiles** (role, instructions, model, allowed tools and skills, clearance role) are claims
  in the `kk-profile` pack; decoders turn one profile into each harness's format (Claude Code agent
  files, A2A Agent Cards). **Skills** are Agent Skills packages; every module may ship one.
- Profiles, skills, models and hook sets are module versions, so the evaluator can vary them like
  any other parameter.

---

## 10. MAPE-K: the loop the pieces already form

Monitor–Analyze–Plan–Execute over Knowledge needs no new component; the spec names the loop and
records each pass.

| Phase | Slots | LLM role |
| --- | --- | --- |
| Monitor | encoders, observation aggregator, self-telemetry | none, except extracting from unstructured reports |
| Analyze | conformance, anomaly, belief/conflict, curriculum processors | explaining deviations, proposing causes — as `inferred` claims with low belief until verified |
| Plan | router: a System 1 recipe if one matches, otherwise an agent profile | proposing the adaptation as a change set |
| Execute | decoder + actuator, gated by clearance and the actuator's action list; model-level changes via review | none; the gates are deterministic |
| Knowledge | the kernel | — |
| **Learn** | evaluator, consolidator | plans that keep working are crystallized into System 1 rules or recipes |

Three scopes: a single case (one process or chain run), a modeled system (a repo, a business), and
the platform itself (its modules, chains and vocabularies). Each pass is an **adaptation record**: an
entity linking symptom claims → plan → change set → run → outcome claims. It is a pattern of
entities and claims, not a new primitive.

---

## 11. Real-time operation

- **Hot path** (chat, live agents): read views and read models (entity cards, cached saved queries),
  System 1 engines, `explain`. Targets to measure in milestone 1: p95 < 100 ms for an entity card on
  a local deployment.
- **Cold path**: validation, apply, belief re-scoring, verification, reasoning — triggered by
  `kernel_change_set` notifications. Target: auto-lane submit → applied in < 2 s locally.
- **Session claims** an agent needs immediately are submitted normally; read models show
  not-yet-applied change sets of the same agent as "pending" when useful.
- Channels: `kernel_change_set` (`{id, status, scope, lane}`), `kernel_redaction` (`{kind, id}` — the
  artifact store deletes bytes on this).

---

## 12. Evaluation

- Eval sets are data (`eval/`), versioned and reviewable. Each case gives an input fixture and the
  expected outcome (change-set ops, a rejection with its code, or an answer with required claim ids).
- The evaluator records results as observed claims about the module: `kk:evalScore` with qualifiers
  `kk:evalSet` and `kk:metric`.
- **Slot competitions:** two modules for the same slot run on the same eval set; single-active slots
  promote the winner. The first competitions are expected in the belief-model and entity-resolver
  slots; the chain runner is chosen by decision (D-31) and checked by its own eval (G4).
- Each milestone ends on gates checked by the evaluator.

---

## 13. Milestones

- **Milestone 1 — the system models itself** (details, status and gates: `docs/milestone-1.md`). The
  self-encoder scans this repository into claims; the validator, belief model and verifier process
  them; the graph decoder shows the self-model; the chain decoder compiles the modeled chains for
  RuleGo-Server, which runs them on kernel events; drift and round-trip checks prove the deployment;
  an agent answers questions about the system's structure with cited claims and belief.
- **Milestone 2 — a real process loop.** Model a repository's build-and-deploy pipeline, decode it to
  BPMN, run it with SpiffWorkflow calling the real CI, bring traces back as OCEL observations, check
  conformance, adapt once through the review lane.
- **Later:** interviews (OntoAgent-style, gap-driven), reasoner (SQL rules, then Nemo), agent profile
  decoders, real-time event transport, consolidator (decay, crystallization), Wikibase or other
  publication targets as decoders.

---

## 14. Licensing posture

Kernel and default distribution: permissive licenses only (Apache-2.0, MIT, BSD, PostgreSQL) plus
LGPL libraries (e.g. SpiffWorkflow). AGPL components (pg_search, pm4py, pm4py-mcp, Windmill) are
optional modules, never defaults. Camunda 8 is excluded (paid licence for production since 8.6).

---

## 15. Where things come from

Research lineage and sources: `docs/sources.md`. Decisions and open questions: `docs/decisions.md`.

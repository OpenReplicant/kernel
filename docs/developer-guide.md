# Developer guide: the moving parts, and where to plug in

For a developer new to the Knowledge Kernel. Read this first, then `docs/architecture.md` (why it is
built this way) and `docs/spec.md` (the full specification). What each module takes in and gives out is
generated in `docs/modules.md`.

## The loop

The system watches inputs, turns them into structured knowledge, notices when something forms in that
knowledge, reacts, and acts on the world. Every arrow below is a module you can swap; only the box in the
middle is the kernel.

```
  sources            ingest           structure                    detect & react                 act
 ─────────        ──────────        ────────────────────────     ─────────────────────────      ─────────
 files, repos,  ─▶ ingest filter ─▶ encoders ─▶ change sets ─▶ │  KERNEL (Postgres)          │ ─▶ decoders
 event logs,       (secrets, PII)   (one per     (claims +      │  claims, artifacts, runs,   │    actuators
 chats, agent                       format)      artifacts)     │  belief, scopes, review     │    (approved
 sessions, APIs,                                                │                             │     plans only)
 sensors, papers                    agents (MCP) ─▶ change sets │  validate ─▶ review ─▶ apply │
                                                                │       │ applied             │ ─▶ recall:
                                                                └───────┼─────────────────────┘    MCP, UI,
                                                                        ▼                          answers
                                                  processors woken by what formed: validator,      that cite
                                                  belief, quote verifier, conformance, anomaly,    claims
                                                  map gaps, learners ─▶ more change sets
```

## The kernel: small, and the only thing that is not a module

One Postgres database (`kernel/schema/001_kernel.sql`, `002_bootstrap.sql`). It holds:

| Table | What it is |
| --- | --- |
| `claim` | one statement: subject, predicate, object (an entity IRI or a literal), qualifiers, evidence type, scope, status |
| `claim_reference` | what the claim came from: an artifact (by sha256) or a URI, with the exact quote |
| `claim_derivation` | the claims an inferred claim was derived from |
| `artifact` | a stored source (file, transcript, model), by content hash |
| `run` | one execution of an agent or module: what it read, what it produced |
| `change_set` | the only way anything is written: a batch of ops, validated, maybe reviewed, then applied |
| `belief`, `verification` | a claim's score, and checks of it by other methods |
| `agent`, `scope`, `scope_grant` | who acts (each with its own database login), what may be seen, who may see it |
| `redaction` | the one way content is removed; identity and lineage stay |

What the kernel enforces, in the database itself: the six invariants (attribution, nothing deleted,
scope never widens, clearance by people, no self-verification, cited answers), the change-set state
machine (submitted → valid → awaiting review → applied), row-level security per scope, and that only
known vocabulary terms are used as predicates.

**Its interface is the `api` schema**: read views (`api.claims`, `api.change_sets`, `api.review_queue`)
and functions (`api.submit_change_set`, `api.start_run`, `api.finish_run`, `api.review`, `api.redact`,
`api.explain`, `api.record_validation`). Every module and agent uses only these, under its own login.
After each change set is inserted or applied, the kernel sends `NOTIFY kernel_change_set
{id, status, scope, lane}`.

## The interfaces (the contract a developer codes against)

| Interface | Where | Used by |
| --- | --- | --- |
| **Change set** (JSON): ops `add_artifact`, `add_entity`, `add_claim`, `set_status`, `add_belief`, `add_verification` | `kernel/schemas/change_set.schema.json` | everything that writes |
| **Module manifest** (`module.yaml`): kind, slots, role, clearance, runtime, `accepts`/`produces`, triggers | `kernel/schemas/module.schema.json`, checked by `scripts/check_manifests.py` and `scripts/catalog.py` | every module |
| **Slots**: the jobs a module can fill (encoder, verifier, recall-index, actuator, ui, trigger, …), each with a contract | `ontology/slots.yaml` | manifests, the evaluator |
| **Vocabulary packs**: the terms claims may use (PROV, BBO, SOSA, ORG, ActivityStreams, Web Annotation, …), one home per concept | `ontology/packs.yaml`, `ontology/*.ttl` | encoders, processors, the validator |
| **`kk.lib`** (Python): connect as your role, `run()`, `submit()`, `listen()` with pattern triggers | `kk/lib.py` | Python modules |
| **MCP tools**: `query_claims`, `get_entity`, `search`, `explain`, `submit_change_set`, `get_change_set`, `start_run`, `finish_run`, `review_queue`, `review`, `graph`, `list_modules`, `list_slots`, `whoami` | `modules/kernel-mcp` | agents in any harness |
| **Skills**: instructions an agent follows to record knowledge correctly | `modules/skill-*/SKILL.md` | harness agents |
| **Harness hooks**: session start, prompt, session end (context in, the session's work out) | `modules/kernel-mcp/kernel_mcp/hooks.py` | Claude Code and similar harnesses |
| **UI views**: a view is a small ES module the UI shell loads | `modules/ui`, `modules/view-*` | people |
| **`kk` command**: `up`, `demo`, `init`, `serve`, `review`, `module install`, `export`/`import`, … | `kk/cli.py` | operators |

## The moving parts, by kind

Each part is a module: a folder under `modules/` with a manifest, its own database role and its own
tests. `kk serve` runs the default ones in one process; `kk module install` runs any one in its own
sandboxed container.

| Kind | What it does | Examples in this repo |
| --- | --- | --- |
| **encoder** | a source format → claims (deterministic where it can be) | `encoder-eventlog` (CSV, XES), `encoder-bpmn`, `encoder-papers`, `encoder-interaction` (chats, agent sessions), `self-encoder` (this repository), `observer-prometheus` (metrics) |
| **processor** | claims → claims, beliefs or verifications; woken by what formed | `validator-shacl`, `belief-corroboration`, `verifier-quotes`, `ingest-filter-gitleaks`, `conformance`, `anomaly`, `map-gaps`, `learner-process`, `recall-index`, `recall-dense` |
| **agent** (skill) | a harness agent following instructions, writing under its own identity | `skill-process-extraction`, `skill-interview`, `skill-adaptation-planning` |
| **interface** | how people and agents reach the kernel | `kernel-mcp`, `ui` and the views `view-modules`, `view-bpm`, `view-devops` |
| **decoder** | claims → a format (BPMN, a report) | BPMN export in `view-bpm`; others parked |
| **actuator** | acts on the world from approved plans only; never writes claims | `actuator-compose` |
| **control** | measures and governs modules | `evaluator` (scores as claims), the review lane, `kk module install` |
| **pack** | vocabulary terms, loaded as claims | `pack-loader` |

Models (LLMs, embeddings) live inside modules at the edges: an agent extracting from an interview, a
dense recall index, a planner. Their output is a change set like any other, validated and often
reviewed, and verified by a different method.

## Reactivity: how parts are woken

Today (D-79): after a change set is applied, the kernel's `NOTIFY` wakes each worker; `kk.lib.listen`
runs one query to see whether the change set added a claim matching the worker's declared pattern
(`S P O`, e.g. `? bbo:has_nextFlowNode ?`), and a periodic sweep catches anything missed. The worker
then recomputes what it needs and submits change sets.

**Proposed (D-83, owner to confirm after a spike): a live graph and detectors in the kernel, reactors
outside.**

```
 recorded data              detectors (in the kernel,          events               reactors (anywhere)
 ─────────────              declarative, run as each           ──────               ───────────────────
 artifacts (raw)  ───▶      change set is applied)      ───▶   kernel.event   ───▶  workers (kk.lib),
 claims           ───▶      · on an artifact: media type       formed |             sandboxed modules,
 graph (live,     ───▶      · on a claim: S P O pattern        dissolved,           agents over MCP,
 kept in sync in            · on a graph shape: an             durable, with        later: webhooks,
 the same txn)              openCypher MATCH                   a cursor per         queues
                                                               reactor
```

- **A live graph inside Postgres**: the current claims projected into a property graph (Apache AGE,
  Apache-2.0, an extension, not a fork) by the kernel in the same transaction that applies each change
  set, so it is never behind. Entities are nodes; current claims with an entity object are edges
  carrying the claim id, predicate, belief and scope; superseded, retracted or redacted claims leave the
  graph (their history stays in the claims). Queried in openCypher, read-only, with row-level security
  (AGE 1.7, which means Postgres 18).
- **Detectors are separate from reactors.** A detector is data (a pattern registered by a module or a
  person), evaluated by the kernel at apply time against everything stored, anchored on what changed;
  it records an event when a match forms or dissolves. Any number of reactors subscribe to a detector's
  events, each with its own cursor, so a pattern is watched once and acted on many ways.
- **Several ways to react**, all reading events and writing change sets: an in-process worker, a
  sandboxed container, an agent through MCP, later a webhook or a queue.
- **Unchanged**: reactors are modules under their own roles; nothing but change sets writes; an event
  carries ids only, so a reactor sees only the claims its clearance allows.

## Adding something

| To add | Do this |
| --- | --- |
| a new source format | copy `modules/_template`, write a pure `encode()` (input → `add_claim` ops) and a `__main__` that stores the artifact and submits change sets; declare `accepts` (media types) and `produces` (terms); a fixture test |
| a processor | read `api.claims` with `kk.lib`, declare a pattern trigger in the manifest, submit derived claims (`inferred`, `derived_from`), retract what no longer holds |
| a vocabulary | cut a subset of a standard ontology with `scripts/vocab_subset.py`, add it to `ontology/packs.yaml`, regenerate `modules/pack-loader/packs.sql` |
| an agent behaviour | a `SKILL.md` and a manifest of kind `agent`; the agent writes under its own login through MCP |
| a UI view | a view module the shell loads (see `modules/view-bpm`) |
| an action on the world | an actuator that reads approved plans and acts; it never writes claims |

Then: `python scripts/check_manifests.py`, `python scripts/catalog.py`, the module's tests, and
`sh scripts/smoke_kernel.sh` (`.claude/skills/kernel-dev/SKILL.md` has the full routine).

## Rules that keep it adaptable

- Modules never write tables; everything goes through change sets, under the module's own login.
- Standard vocabularies first; `kk:` only for the module contract.
- Every claim says who made it, from what, with what quote; nothing is deleted.
- People grant clearance and approve what models propose; no method checks its own output.
- Permissive licences in the default distribution; versions pinned.

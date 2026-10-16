# Milestone 1 — the kernel, on its own

**Goal.** A small kernel that drops into any agent harness (D-36): Postgres with the frozen kernel
schema, the conventions (standard vocabularies, change-set and module schemas, slot catalog), and
swappable default modules for its three jobs: **ingestion** (encoders, ingest filter), **processing**
(validator, belief) and **recall** (MCP tools; hooks). Integration is a ladder: MCP only → + skill
file → + harness hooks → + further modules. Nothing else is required: no runner, no PostgREST, no UI.

**History.** T0–T10 built the first version of the full distribution (self-model, runners, graph UI,
RuleGo decoder). D-36 narrowed the milestone to the kernel; the runner, decoder, graph and UI modules
are parked under `parked/` (out of CI) and come back as modules when wanted.

Work in order; each task ends with tests passing and a line in `docs/decisions.md` if anything was decided.
The kernel gate (G4) is checked by `scripts/smoke_kernel.sh` locally and in CI, without Docker.

## Tasks

| # | Task | Done when | Status |
| --- | --- | --- | --- |
| T0 | Initialise the repo from this handoff; CI runs `kernel/schema/tests/run.sh`, `shapes/tests/test_shapes.py` and `scripts/check_manifests.py` | CI green | done (#38) |
| T1 | **Spike (timebox: 1 day):** can RuleGo-Server's MCP mode pass the caller's identity into a chain? Pin a RuleGo-Server version, follow `deploy/rulego/README.md` | Result recorded as decision (closes O-7) | done (#38, D-21) |
| T2 | Bring up `db`, `postgrest`, `caddy` with `deploy/compose.yaml`; pin images in `.env`; write one migration per module creating its `kkm_*` role and agent row (pattern: `modules/_template/migration.sql`); mint JWTs | `GET /api/claims` works with a module JWT and fails without one | done (#39) |
| T3 | **Pack loader**: install `kk-core.ttl`, `kk-tosca.ttl`, `kk-profile.ttl` (typing claims make their properties approved) and turn `ontology/slots.yaml` into `kk:Slot` claims | every kk/kkt property used by the self-encoder is approved; 42 slot entities exist | done (#40) |
| T4 | **Validator module** (protected): JSON Schema + RDF conversion of the change set + pySHACL with `shapes/kernel.shacl.ttl`; calls `record_validation` | its eval: valid fixtures accepted, each invalid fixture rejected with the right reason | done (#41) |
| T5 | **Python bootstrap chain runner** (stand-in until RuleGo runs the chains): runs chain YAML (`kernel/schemas/chain.schema.json`); a kernel run per kernel-writing step with `plan_step` = step IRI (D-25) | validator chains run end to end on submitted change sets | done (#42); retired by D-31; parked (D-36) |
| T6 | **Ingest filter** (gitleaks) and **self-encoder**: scan this repository. Files become artifacts; claims (evidence `deterministic`, each referencing its file artifact and line span): module manifests → `kk:Module` + `kk:fills` + requirements + interfaces; chains → `kk:Chain`, `kk:ChainStep`, `kk:next` with `kk:onLabel` qualifiers; compose services → `kkt:NodeTemplate` + `kkt:dependsOn`; `.ttl` files → packs; SQL migrations → schema objects | G2 | done (#43, G2) |
| T7 | **Belief model v0** (corroboration counting: independent sources per statement key, minus conflicts on single-value properties, validity windows respected) | its eval: copied sources don't inflate belief; changes over time aren't conflicts | done (#44) |
| T8 | **kernel-mcp** on MCP Python SDK v2: tools in `modules/kernel-mcp/module.yaml`, forwarding the caller's token; connect Claude Code with `deploy/mcp/agents.mcp.json` | an agent reads, explains and submits through MCP under its own identity | done (#45) |
| T9 | **Graph decoder** + UI graph tab (Cytoscape.js): nodes = entities, edges = entity-valued claims, styled by status and belief; filter by module, slot, chain | G3 | done (#46, G3); parked (D-36) |
| T10 | **Chain decoder → RuleGo DSL**: compile approved chain claims; report unmappable steps (`kk:runnable false` + reason); write to the runner's chain folder; record `kk:deployedHash`; drift check | G5 | done (#47, G5); parked (D-36) |
| T11 | **Kernel alone** (D-36): kernel-mcp talks to Postgres directly (stdio with the agent's own login, or `--http` with per-request JWTs; D-37); the validator starts itself (LISTEN + sweep); PostgREST optional; runner, decoder, graph and UI modules parked | G4 | done |
| T12 | **Standard vocabularies**: replace `kk:` terms wherever a standard owns the concept (PROV, DQV, EARL, ECO, SKOS/SSSOM, P-Plan, DOAP/SPDX); kernel bookkeeping (status, scope, statement key, belief) stays in columns and is mapped on export; TOSCA mirror and agent-profile packs leave core | `kk:` reduced to the module contract; packs, shapes, encoder and tests updated | next |
| T13 | **Ingestion without a runner**: encoders and the ingest filter start themselves or are called by the harness; the home-grown chain format is retired in favour of encoders that read runners' native formats into P-Plan claims; re-scans supersede changed claims | the self-model scan runs with only the kernel stack; G2 | |
| T14 | **Processing without a runner**: the belief model listens for applied change sets (plus a sweep) and scores their claims | every applied claim gets a belief within 2 s locally | done (D-38; done before T12, T13) |
| T15 | **Harness kit**: the skill file, Claude Code hooks (recall at session start and on prompt; record the session's run and claims at the end), and a one-page drop-in guide (`docs/harness.md`) | a fresh Claude Code session with only the kit and the kernel stack recalls and records | |
| T16 | **Self-questions eval**: 10+ questions about the system's own structure in `eval/self-model/questions.yaml` | G7 | |

## Gates

| Gate | Check |
| --- | --- |
| G1 | Kernel tests, shape tests and manifest validation pass in CI |
| G2 | **Self-model complete**: every module manifest, compose service and `.ttl` file has claims; 0 claims without a reference; every `kk:Module` fills at least one slot (SHACL). Its check (`parked/scripts/check_g2.py`) returns with T13 |
| G3 | Parked (D-36): the self-model renders as a graph in the UI from live claims |
| G4 | **The kernel alone**: with only Postgres, the validator, the belief model and kernel-mcp over stdio, a fresh agent session submits a claim, sees it applied and scored (target < 2 s, spec §11) and cites it with id and belief; invalid change sets come back with the reason; forged attribution is refused, stdio refuses a login that can switch roles, and the agent's login cannot become the owner; a person approves a review-lane change set. `scripts/smoke_kernel.sh` |
| G5 | Parked (D-36): drift of a deployed RuleGo chain file is detected within one check interval |
| G6 | Parked (D-36): round-trip verification of chains |
| G7 | An agent connected over MCP answers the self-questions citing claim ids and showing belief; ≥ 90% correct with all citations resolvable via `explain` |

## Out of scope for milestone 1

Chain runners and decoders (parked), the UI, BPMN and process runners, interviews, LLM extraction,
reasoner, agent-profile decoders, pgvector recall, OIDC sign-in for people.

# Milestone 1 — the system models itself

**Goal.** Starting from bare Postgres, the platform scans its own repository, models itself as claims
(modules, slots, chains, deployment, vocabularies), shows that model as a graph, compiles its own
chains to RuleGo DSL, and hands its entrypoint to RuleGo-Server — with evidence that nothing changed in
behaviour when it did.

**Why this first.** It exercises every layer with deterministic evidence and no outside data: an
encoder, processors (validator, belief, verifier), two decoders (graph, chain), an actuator-like
handover (runner deployment), the API, MCP and UI interfaces, and the evaluator.

Work in order; each task ends with tests passing and a line in `docs/decisions.md` if anything was decided.
Every gate so far is checked by `scripts/smoke_stack.sh` (full stack in Docker) and in CI.

## Tasks

| # | Task | Done when | Status |
| --- | --- | --- | --- |
| T0 | Initialise the repo from this handoff; CI runs `kernel/schema/tests/run.sh`, `shapes/tests/test_shapes.py` and `scripts/check_manifests.py` | CI green | done (#38) |
| T1 | **Spike (timebox: 1 day):** can RuleGo-Server's MCP mode pass the caller's identity into a chain? Pin a RuleGo-Server version, follow `deploy/rulego/README.md` | Result recorded as decision (closes O-7) | done (#38, D-21) |
| T2 | Bring up `db`, `postgrest`, `caddy` with `deploy/compose.yaml`; pin images in `.env`; write one migration per module creating its `kkm_*` role and agent row (pattern: `modules/_template/migration.sql`); mint JWTs | `GET /api/claims` works with a module JWT and fails without one | done (#39) |
| T3 | **Pack loader**: install `kk-core.ttl`, `kk-tosca.ttl`, `kk-profile.ttl` (typing claims make their properties approved) and turn `ontology/slots.yaml` into `kk:Slot` claims | every kk/kkt property used by the self-encoder is approved; 42 slot entities exist | done (#40) |
| T4 | **Validator module** (protected): JSON Schema + RDF conversion of the change set + pySHACL with `shapes/kernel.shacl.ttl`; calls `record_validation` | its eval: valid fixtures accepted, each invalid fixture rejected with the right reason | done (#41) |
| T5 | **Python bootstrap chain runner** (stand-in until RuleGo runs the chains): runs chain YAML (`kernel/schemas/chain.schema.json`); a kernel run per kernel-writing step with `plan_step` = step IRI (D-25) | validator chains run end to end on submitted change sets | done (#42); retired by D-31 |
| T6 | **Ingest filter** (gitleaks) and **self-encoder**: scan this repository. Files become artifacts; claims (evidence `deterministic`, each referencing its file artifact and line span): module manifests → `kk:Module` + `kk:fills` + requirements + interfaces; chains → `kk:Chain`, `kk:ChainStep`, `kk:next` with `kk:onLabel` qualifiers; compose services → `kkt:NodeTemplate` + `kkt:dependsOn`; `.ttl` files → packs; SQL migrations → schema objects | G2 | done (#43, G2) |
| T7 | **Belief model v0** (corroboration counting: independent sources per statement key, minus conflicts on single-value properties, validity windows respected) | its eval: copied sources don't inflate belief; changes over time aren't conflicts | done (#44) |
| T8 | **kernel-mcp** on MCP Python SDK v2: tools in `modules/kernel-mcp/module.yaml`, forwarding the caller's token; connect Claude Code with `deploy/mcp/agents.mcp.json` | an agent reads, explains and submits through MCP under its own identity | done (#45) |
| T9 | **Graph decoder** + UI graph tab (Cytoscape.js): nodes = entities, edges = entity-valued claims, styled by status and belief; filter by module, slot, chain | G3 | done (#46, G3) |
| T10 | **Chain decoder → RuleGo DSL**: compile approved chain claims; report unmappable steps (`kk:runnable false` + reason); write to the runner's chain folder; record `kk:deployedHash`; drift check | G5 | done (#47, G5) |
| T11 | **Verifier v0 (round-trip)**: decode each chain from claims, re-encode the result, compare statement keys; submit `add_verification` (a different module from the encoder) | G6 | next |
| T12 | **Handover to RuleGo-Server and to events** (D-31, D-32, D-33): build RuleGo-Server v0.38.0 with `with_extend` (pinned, from source); `notify` chains start from the `endpoint/pgNotify` component of our build `kk-rulego` (D-35); validator and belief chains become event-triggered (belief scores each applied change set, no watermark); Python steps compile to `x/python`, `http` triggers to RuleGo's HTTP endpoint; remove the Python runner | G4 | |
| T13 | **Self-questions eval**: 10+ questions about the system's own structure in `eval/self-model/questions.yaml` ("which modules fill the shape-validator slot?", "what does the validate-one chain call after validate?", "which services depend on postgrest?") | G7 |  |

## Gates

| Gate | Check |
| --- | --- |
| G1 | Kernel tests, shape tests and manifest validation pass in CI |
| G2 | **Self-model complete**: every module manifest, chain, compose service and `.ttl` file has claims; 0 claims without a reference; every `kk:Module` fills at least one slot (SHACL) |
| G3 | The self-model renders as a graph in the UI from live claims |
| G4 | **RuleGo runs the system**: with only RuleGo-Server and the bridge running chains, every chain is runnable or carries its reason; on the eval fixtures every change set reaches its expected outcome (status, resulting statement keys), triggered by events; every run links to a step IRI; submit → applied latency recorded (target < 2 s, spec §11) |
| G5 | Hand-editing a deployed RuleGo chain file is detected as drift within one check interval |
| G6 | Round-trip verification passes for every chain; failures name the unmappable step |
| G7 | An agent connected over MCP answers the self-questions citing claim ids and showing belief; ≥ 90% correct with all citations resolvable via `explain` |

## Out of scope for milestone 1

BPMN and SpiffWorkflow (milestone 2), interviews, LLM extraction, reasoner, agent-profile decoders,
real-time event transport, pgvector recall, OIDC sign-in for people.

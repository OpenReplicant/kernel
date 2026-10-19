# Modules: what each takes in and gives out

Generated from the manifests by `python scripts/catalog.py` (D-78); CI fails when it is stale. An
**encoder** turns a source format (media type) into claims; a **decoder** turns claims into a format;
a **processor** reads claims (terms) and writes claims, beliefs or verifications; an **actuator**
acts on the world from approved claims; an **agent** module is a skill a harness agent follows.
Terms are CURIEs of the vocabulary packs (`ontology/packs.yaml`); ops are change-set operations.

## Encoders

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **encoder-bpmn** 0.1.0<br>BPMN encoder | encoder | `application/bpmn+xml` (BPMN 2.0) | `add_artifact`<br>`add_claim`<br>`set_status`<br>`bbo:Process`<br>`bbo:Task` (and the other flow-node classes)<br>`bbo:has_nextFlowNode`<br>`bbo:has_container`<br>`bbo:Role`<br>`bbo:has_resource`<br>`rdfs:label` | manual |
| **encoder-eventlog** 0.1.0<br>Event-log encoder | encoder, observation-aggregator | `text/csv` (case, activity, timestamp, resource columns)<br>`application/xml` (XES event log) | `add_artifact`<br>`add_claim`<br>`set_status`<br>`bbo:has_nextFlowNode` (observed)<br>`bbo:has_container`<br>`sosa:hasSimpleResult` (counts and waits per window, sosa:observedProperty urn:kk:property:*)<br>`rdfs:label` | manual |
| **encoder-interaction** 0.2.0<br>Interaction encoder | encoder | `application/jsonl` (chat: one message or action per line)<br>`application/jsonl` (a Claude Code session transcript) | `add_artifact`<br>`add_claim`<br>`set_status`<br>`text/plain` (the rendered transcript, the artifact every claim quotes)<br>`as:Note`<br>`as:content`<br>`as:attributedTo`<br>`as:context`<br>`as:inReplyTo`<br>`as:published`<br>`prov:Activity`<br>`as:actor`<br>`as:instrument`<br>`prov:used`<br>`as:Person`<br>`as:Application`<br>`as:Service`<br>`as:OrderedCollection`<br>`dcterms:type`<br>`dcterms:isPartOf` | manual |
| **encoder-papers** 0.1.0<br>Paper encoder | encoder | `application/jsonl` (one paper per line: SciFact or BEIR) | `add_artifact`<br>`add_claim`<br>`fabio:ResearchPaper`<br>`dcterms:title`<br>`dcterms:abstract` | manual |
| **observer-prometheus** 0.1.0<br>Metrics observer: Prometheus | observation-aggregator, source-connector | `application/json` (Prometheus HTTP API query results) | `add_claim`<br>`sosa:hasSimpleResult` (per window, sosa:observedProperty urn:kk:property:*) | schedule: every KK_WINDOW_SECONDS |
| **self-encoder** 0.3.0<br>Repository encoder (self-model first) | source-connector, encoder | `inode/directory` (a repository checkout, git or not)<br>`text/markdown` (documents and their headings)<br>`application/toml` (pyproject.toml, Cargo.toml)<br>`application/json` (package.json)<br>`text/plain` (requirements.txt, go.mod) | `add_artifact`<br>`add_claim`<br>`schema:SoftwareSourceCode`<br>`schema:softwareRequirements`<br>`schema:version`<br>`dcterms:hasPart`<br>`dcterms:title` | manual<br>schedule: on git change (e.g. a git hook or the harness) |

## Processors

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **anomaly** 0.1.0<br>Anomaly processor | reasoner | `sosa:Observation` (sosa:hasSimpleResult per window)<br>`bbo:maxValue` (documented limits) | `add_claim`<br>`oa:Annotation` (dcterms:type urn:kk:anomaly-kind:*, oa:hasTarget the node, oa:bodyValue) | pattern: ? sosa:hasSimpleResult ?<br>pattern: ? bbo:maxValue ?<br>schedule: every KK_SWEEP_SECONDS (sweep; NOTIFY is not durable) |
| **belief-corroboration** 0.4.0<br>Belief model: corroboration counting v0 | belief-model | `claim`<br>`verification`<br>`cito:supports`<br>`cito:refutes` | `add_belief` | notify: kernel_change_set<br>schedule: every 30s (sweep; NOTIFY is not durable) |
| **conformance** 0.2.0<br>Conformance processor | reasoner | `bbo:has_nextFlowNode` (documented and observed)<br>`sosa:Observation` (directly-follows counts, waits, case counts)<br>`bbo:maxValue` (documented limits) | `add_claim`<br>`set_status`<br>`oa:Annotation` (a discrepancy: dcterms:type urn:kk:discrepancy-kind:*, oa:hasTarget the steps, oa:bodyValue) | pattern: ? bbo:has_nextFlowNode ?<br>pattern: ? sosa:hasSimpleResult ?<br>pattern: ? bbo:maxValue ?<br>schedule: every KK_SWEEP_SECONDS (sweep; NOTIFY is not durable) |
| **ingest-filter-gitleaks** 0.2.0<br>Ingest filter: secrets (gitleaks) | ingest-filter | `*/*` (files about to be stored) | `artifact` (only what passes; findings are reported, never stored) | manual |
| **learner-process** 0.1.0<br>Process learner | consolidator | `oa:Annotation` (a confirmed discrepancy)<br>`sosa:Observation` (case counts) | `add_claim`<br>`bbo:has_nextFlowNode` (learned, inferred)<br>`oa:Annotation` (dcterms:type urn:kk:revision-kind:add-path) | pattern: ? dcterms:type urn:kk:discrepancy-kind:*<br>schedule: every KK_SWEEP_SECONDS (sweep; NOTIFY is not durable) |
| **map-gaps** 0.1.0<br>Map gap processor | reasoner | `bbo:has_container` (steps extracted from what people said)<br>`bbo:has_nextFlowNode`<br>`bbo:has_resource` (the post that does a step)<br>`org:Post`<br>`org:holds`<br>`org:heldBy`<br>`org:reportsTo`<br>`org:postIn`<br>`org:hasPost` | `add_claim`<br>`set_status`<br>`oa:Annotation` (a gap: dcterms:type urn:kk:gap-kind:*, oa:hasTarget the step or post, oa:bodyValue the question) | pattern: ? bbo:has_container ?<br>pattern: ? bbo:has_nextFlowNode ?<br>pattern: ? bbo:has_resource ?<br>pattern: ? org:* ?<br>pattern: ? rdf:type org:Post<br>schedule: every KK_SWEEP_SECONDS (sweep; NOTIFY is not durable) |
| **recall-dense** 0.1.0<br>Recall: dense embeddings fused with BM25 | recall-index | `claim`<br>`redaction` | `application/json` (api.recall_hybrid: ranked claim ids) | notify: kernel_change_set<br>notify: kernel_redaction<br>schedule: every KK_SWEEP_SECONDS |
| **recall-index** 0.1.0<br>Recall index: BM25 | recall-index | `claim`<br>`redaction` | `application/json` (api.recall: ranked claim ids) | notify: kernel_change_set<br>notify: kernel_redaction<br>schedule: every KK_SWEEP_SECONDS |
| **truth-maintenance** 0.1.0<br>Truth maintenance | verifier | `claim` (with derived_from)<br>`set_status` | `add_verification` | notify: kernel_change_set<br>schedule: every KK_SWEEP_SECONDS |
| **validator-shacl** 0.3.1<br>Change-set validator (JSON Schema + SHACL) | shape-validator | `change_set` | `change_set` (its validation report and status) | notify: kernel_change_set<br>schedule: every 30s (sweep; NOTIFY is not durable) |
| **verifier-quotes** 0.2.0<br>Quote verifier | verifier | `claim` (extracted, with a quote)<br>`artifact` | `add_verification` | notify: kernel_change_set<br>schedule: every KK_SWEEP_SECONDS |

## Actuators

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **actuator-compose** 0.1.0<br>Actuator: compose services | actuator, orchestrator-driver | `sosa:Actuation` (an approved plan step: dcterms:type scale or restart, sosa:hasFeatureOfInterest the service) | `run` (what it did, before and after; it never writes claims) | notify: kernel_change_set (applied) |

## Agents

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **skill-adaptation-planning** 0.1.0<br>Skill: plan an adaptation | agent-skill | `oa:Annotation` (an anomaly) | `add_claim`<br>`pplan:Plan`<br>`sosa:Actuation` (plan steps, review lane) | manual |
| **skill-interview** 0.1.0<br>Skill: interview a person to map the organisation and its processes | agent-skill | `as:Note` (the person's messages, recorded by the interaction encoder)<br>`oa:Annotation` (the gaps: dcterms:type urn:kk:gap-kind:*) | `add_claim`<br>`org:Post`<br>`org:OrganizationalUnit`<br>`org:FormalOrganization`<br>`prov:Person`<br>`org:holds` (extracted, with the quote)<br>`org:reportsTo`<br>`org:postIn`<br>`org:unitOf`<br>`bbo:has_nextFlowNode` (extracted, with the quote)<br>`bbo:has_resource` (the post that does a step) | manual |
| **skill-process-extraction** 0.1.0<br>Skill: extract processes from text | agent-skill | `text/markdown` (a written procedure)<br>`text/plain` | `add_artifact`<br>`add_claim`<br>`bbo:has_nextFlowNode` (extracted, with the quote)<br>`bbo:maxValue` (time limits, with the quote) | manual |

## Controls

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **evaluator** 0.1.0<br>Evaluator | evaluator | `application/json` (an eval run file) | `add_artifact`<br>`add_claim`<br>`dqv:value` (dqv:isMeasurementOf, dqv:computedOn as qualifiers) | manual |

## Interfaces

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **kernel-mcp** 0.8.0<br>Kernel MCP server | agent-interface | `claim`<br>`change_set`<br>`run` | `add_claim` (and every op, as the calling agent)<br>`as:Note`<br>`as:content`<br>`dcterms:type`<br>`dcterms:subject`<br>`application/json` (MCP tool results) | — |
| **ui** 0.2.0<br>Web UI shell | ui | `claim`<br>`change_set` | `text/html` (the shell at /ui) | — |
| **view-bpm** 0.2.0<br>Processes | ui | `bbo:has_nextFlowNode`<br>`sosa:Observation`<br>`oa:Annotation` | `text/html` (the Processes view)<br>`application/bpmn+xml` (the documented process, learned paths included) | — |
| **view-devops** 0.1.0<br>Devops | ui | `sosa:Observation`<br>`oa:Annotation`<br>`sosa:Actuation`<br>`run` | `text/html` (the Devops view) | — |
| **view-modules** 0.1.0<br>Modules | ui | `kk:Module` | `text/html` (the Modules view) | — |

## Packs

| Module | Slots | Takes in | Gives out | Runs on |
| --- | --- | --- | --- | --- |
| **pack-loader** 0.1.0<br>Vocabulary pack loader | vocabulary-pack | `text/turtle` (vocabulary pack files) | `add_claim`<br>`rdfs:Class`<br>`rdf:Property`<br>`skos:Concept` | — |


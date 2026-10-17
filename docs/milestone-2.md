# Milestone 2 — BPM and devops on the kernel

**Goal.** The first two target uses (milestone 1, "Target uses") running as modules on the unchanged
kernel, each with a demo that a person can follow in the UI from source to cited answer:

1. **BPM with automated ingestion.** Event logs and process documents become claims; the observed
   process is compared with the documented one; every discrepancy lands in the review lane with its
   evidence, and a process map shows both.
2. **Devops (MAPE-K + LLM).** A running system is observed (metrics aggregated per time window), anomalies
   are claimed, a harness agent proposes an adaptation as a change set, a person approves it, an
   actuator carries it out with a fixed action list, and the effect is observed. Every step cites claims.

Rules carry over from milestone 1: kernel v0 stays frozen; every new module runs in its own sandbox
(`kk module install`, D-43); standard vocabularies first (D-40); permissive licenses in the default
distribution (D-11). The LLM is the harness's: modules don't call models; extraction and planning are
agent work guided by skill files, recorded under the agent's own identity.

## Licenses checked (Oct 2026)

| Library | License | Use |
| --- | --- | --- |
| pm4py (process mining) | AGPL-3.0 (commercial license from PIS) | optional module only, never in the default distribution; the default discovery is our own small Python (directly-follows graph with frequencies and times) |
| bpmn-js (BPMN modeller/viewer) | bpmn.io license: the watermark must stay visible and unchanged | not in the default distribution; maps are drawn with Cytoscape.js (MIT); diagram-js (MIT) if a BPMN editor is ever needed |
| Prometheus (HTTP query API), OpenTelemetry Collector | Apache-2.0 | the observer reads Prometheus's query API; OpenTelemetry later |

## Tasks

| ID | Task | Check | Status |
| --- | --- | --- | --- |
| M2-T0 | **Vocabularies and catalog pointers**: pick the process and observation vocabularies from existing ones (candidates: BBO for BPMN, P-Plan and PROV-O for plans and executions, SOSA/SSN for observations, schema.org for organisations) after checking LOV; add the upstream catalogs (LOV, OBO Foundry, BioPortal, schema.org) to `ontology/packs.yaml` and the look-up rule to the skill | packs install; check_packs; a decision entry per vocabulary | done (D-47: BBO, SOSA, mappings, catalogs) |
| M2-T1 | **Event-log encoder** (command module): CSV or XES in; cases, activities, resources and the directly-follows relation with counts and durations per time window out, as observed claims referencing the log artifact | a sample log gives the expected activities and edge counts; re-ingest supersedes | done (D-48; `scripts/e2e_bpm.py`, sandboxed in `e2e_module.sh`) |
| M2-T2 | **Process document encoders**: BPMN 2.0 XML (deterministic parser → documented process: tasks, gateways, sequence flows) and a skill for agents to extract process claims from text and interview transcripts (extracted, with quotes) | a sample BPMN gives the expected plan; extracted claims without quotes are refused | done (D-49; `scripts/e2e_bpm.py`) |
| M2-T3 | **Conformance processor** (worker): compares documented and observed processes; claims each discrepancy (skipped step, unexpected path, rework, threshold differences) with derivations from the claims it compared; discrepancies wait in the review lane | the sample log and BPMN give the planted discrepancies, each citing its evidence | done (D-50; `scripts/e2e_bpm.py`, sandboxed in `e2e_module.sh`) |
| M2-T4 | **BPM view** (view module): the process map (documented vs observed, frequencies, discrepancies highlighted), each edge explaining its claims | Playwright: map renders; a discrepancy explains | done (D-51; `scripts/e2e_ui.mjs`) |
| M2-T5 | **Metrics observer** (worker, devops): queries Prometheus's HTTP API on a schedule and records observed claims per service and window (SOSA observations), linked to the compose self-model's nodes. Needs network beyond Postgres: the manifest declares the hosts and `kk module install` adds exactly that network (decision) | a demo stack with Prometheus gives windowed observations for each service | done (D-52; `scripts/e2e_devops.sh`) |
| M2-T6 | **MAPE-K loop**: an anomaly processor (thresholds and z-scores per window → anomaly claims derived from observations); a skill for the harness agent to plan adaptations as change sets (review lane); an actuator (orchestrator-driver slot) that carries out approved plans with a fixed action list (restart, scale) and records what it did; it never writes claims about its own success (the observer does) | demo: a stressed service → anomaly → proposed plan → approved → scaled → recovery observed; every step cites claims | next |
| M2-T7 | **Devops view** (view module): service topology with current observations and anomalies, and the MAPE-K timeline (observed → analysed → planned → approved → executed → observed) | Playwright: the timeline of the demo run renders and explains | |

## Gates

| Gate | Check |
| --- | --- |
| M2-G1 | **BPM demo**: from a sample event log and BPMN file, a person sees the observed and documented process in the UI and reviews every planted discrepancy, each citing its evidence with belief |
| M2-G2 | **Devops demo**: the MAPE-K run above end to end on the compose stack, with a person's approval as the only manual step, and every claim in the timeline cited |
| M2-G3 | Every new module installs with `kk module install` and runs sandboxed; the kernel tests, G4 and G7 still pass |

## Decided by default (D-47 e)

- BPM sample data: a small synthetic order-to-cash event log and BPMN model written for the demo, with
  planted discrepancies; a public BPI Challenge log or one of the owner's can replace it later.
- Devops demo system: our own compose stack with Prometheus added.

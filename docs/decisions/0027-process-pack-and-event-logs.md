# 0027. The process pack and the event-log adapter

Date: 2026-10-06 · Status: accepted

## Context

Phase 3 starts with a general process pack (ADR 0026, item 1). It needs:
- a representation that workflow adapters can compile into Camunda, n8n, RuleGo or code;
- three views of the same process: as told, as written and as done.

The core already defines the kinds `process`, `activity`, `gateway`, `role`, `data` and
`component`, and the kernel edge `flows_to`. `bpm-reference` is a toy pack, frozen for the
kernel's own tests.

Process mining tooling constrains how "as done" is read. pm4py is AGPL-3.0, so under
ADR 0012 it cannot be imported. A directly-follows graph and per-case replay need no
library at all.

## Decision

**The `process` pack** (`packs/process/`, namespace `process`) is the general pack.
- **Core kinds it allows:** `process`, `activity`, `gateway`, `role`.
- **Kinds it adds:**
  - `unit`: a department, team or other organisational unit;
  - `trigger`: what starts a case, or what a step waits for (a request, a message, a
    schedule);
  - `outcome`: how a case ends;
  - `kpi`: a measure of a process or step.
- **Edge kinds it adds:**
  - `performed_in`: a step is done in a system;
  - `handles`: a step or process works on a business object; `props.qualifier` says how
    when the source does;
  - `measures`: a KPI's observed value, in `props.value` and `props.unit`, over the
    edge's validity window.
- **Placement:** systems (`component`) and business objects (`data`) stay in `core`.
- **bpm-reference** stays as the kernel's test pack. Pack term names are global and every
  pack installs beside the others, so this pack's names differ from its `org_unit` and
  `uses`.

**Steps and conditions, not BPMN.** A process is a graph of steps, not a diagram.
- **Nodes:**
  - steps: `activity` and `gateway`;
  - the edges of the process: `trigger` and `outcome`.
- **Membership:** each node is `part_of` its process.
- **Flows:** `flows_to` joins them.
- **Conditions:**
  - A flow out of a decision carries `props.when`. It is an expression over the case's
    attributes in a small, runtime-neutral grammar:
    - comparisons `= != < <= > >=` and `in [...]`;
    - joined with `and`, `or`, `not` and parentheses;
    - the literal `else` for the default branch.
  - It is written in canonical form, so two sources stating the same condition assert the
    same edge.
  - The source's own words stay in the claim text.
- **Meaning:**
  - A flow with `when` is a routing rule: every case at its start for which the condition
    holds continues at its end.
  - A flow without `when` says only that work passes from one step to the next.
- **Gateways:**
  - A gateway whose flows out carry conditions is a decision.
  - A gateway whose flows out carry none is a parallel split.
  - Inclusive gateways come later.
- **Who and where:**
  - who does a step: `role responsible_for activity`, as in bpm-reference;
  - who holds a role: `implements`;
  - where a step is done: `performed_in`;
  - what it works on: `handles`.
- **Translation:** each runtime gets a workflow adapter that translates this graph both
  ways (ADR 0026, item 4). BPMN is one more adapter, not the model.

**Three views are sources.**
- *As told:* interview turns (interview skill).
- *As written:* SOPs and documents (extractor, parsers at the edge).
- *As done:* event logs, read by the adapter below.

Each view has its own origins, so belief counts each view once. Comparing the views is a
query over the sources and belief of each step and flow. A fact one view asserts and
another denies is contested; a fact only one view states is shown as such.

**The event-log adapter, `wmk-process`** (`packs/process/adapter/`), is deterministic and
writes through the gateway, like `wmk-software`.
- **Inputs:** CSV, XES and OCEL 2.0 JSON, read with the standard library. An OCEL log is
  flattened on one object type, the case notion.
- **Configuration** (a YAML file):
  - the process name;
  - the column names;
  - a map from the log's activity labels to step names;
  - the system the log comes from;
  - the business object a case is.
- **`discover` writes the view as done**, without reading the model. It ingests a digest of
  the log, a deterministic text with one line per fact, and cites its lines. The digest
  covers activities, directly-follows counts with example cases, roles and measures. It
  writes observed claims:
  - each activity `part_of` the process;
  - a `flows_to` for each directly-follows pair seen at least `min_count` times;
  - `role responsible_for activity` from `org:role`;
  - `activity performed_in component`;
  - `process handles data`;
  - KPIs: cases, median cycle time and executions per activity.

  Counts go in the claim text, not in edge props, so a newer export reasserts the same
  edges. Each export is mapped in an extraction run in a stable collection, so closing the
  run retracts what the export no longer shows.
- **`conform` checks the mapped process against the log.** It reads the process through
  `query_graph` and records the offset it read at. It writes a conformance digest and one
  observed verdict on each existing edge it checks, by `edge_id`. A verdict is always an
  assertion or a denial, so a later run can overturn an earlier denial: closing a run
  retracts only the earlier run's assertions.
  - A mapped activity: its `part_of` the process is asserted if some case ran it, and
    denied if none did.
  - A mapped flow between two activities: asserted if some case followed it, and denied
    if none did while the first ran.
  - A decision's branch (a flow from a gateway, with `when`) is checked case by case.
    Direct flows the log maps to the steps the decision branches to do not bypass it.
    - The case reaches the gateway from an activity that flows into it, unless it
      continues along one of that activity's other flows: a review that sends a request
      back for revision never reaches the decision on its amount.
    - The branch expected is the first condition that holds (the `else` branch if none).
    - The step that follows in the log is the branch taken.
    - The branch is asserted when every case that met its condition took it. It is
      denied when some did not, with the counts and example cases in the claim text.
  - Conditions on attributes the log lacks, chains of gateways and parallel splits are
    listed in the digest as not checked.

  Where the interview or the SOP asserts what the log denies, the edge is contested; that
  is the discovery deliverable. Flows the log shows and nobody mapped carry only the log's
  support.
- **No people yet.** `org:resource` is not mapped: names are personal data, and a source
  about people must list its subjects and be sealed (ADR 0022), which needs their agents
  first. Roles are mapped.

**A shared adapter kit.**
- `wmk-software`'s plan, apply and fixture-script code moves to `adapter-kit/`
  (`wmk-adapter-kit`, a workspace member), with assertions on an existing `edge_id` added.
- `wmk-software` keeps its module names.
- `wmk-process` uses the kit too.

**The fixture is Northwind's purchase requests.** A synthetic log is generated from a
fixed seed: 120 cases from January to September 2026. Urgent requests over 10,000 euros
skip the controller's approval and are approved after the purchase order. The tests write
an SOP's view of the same process through the gateway and check that `conform` makes the
skipped branch contested.

## Consequences

The pack gives Phase 3 a representation that workflow adapters can compile, and an "as
done" view in its graph, without new kernel features or new dependencies.

Corrections to ADR 0026 and CLAUDE.md:
- pm4py-mcp runs only as a separate service that an operator installs after legal review
  (ADR 0012).
- Camunda 8 is licensed for non-production use without a paid licence. Operaton (the
  Apache-2.0 fork of Camunda 7) is the open BPMN runtime.

Not in this step:
- BPMN import and export;
- alignments;
- conditions checked across chains of gateways;
- inclusive gateways;
- people from `org:resource`;
- object-centric relations beyond the case object;
- KPIs beyond the three above;
- the SOP and interviews of the Northwind demo (ADR 0026, item 5).

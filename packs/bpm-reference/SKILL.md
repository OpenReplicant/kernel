---
name: bpm-reference
description: >
  Toy business-process pack for the World Model Kernel, used by the CI fixtures and the
  MVP demo. Use when mapping how work flows through an organisation: processes, their
  activities and decision gateways, the roles and units responsible, the systems and data
  they use, and who holds each role over time.
metadata:
  kernel: ">=0.1 <1.0"
  namespace: bpm
---

# Business processes (reference pack)

Put process structure in the `bpm` namespace. Allowed kinds there: `process`,
`activity`, `gateway` (a decision point), `role`, `org_unit` (department or team).
Systems (`component`) and business objects (`data`, such as an invoice) stay in `core`.

| Fact in the source | Operations |
| --- | --- |
| A step belongs to a process | `activity part_of process` |
| Step A is followed by step B | `A flows_to B` (activities and gateways only) |
| A decision ("if the amounts match") | a `gateway`, with `flows_to` in and out |
| Who does a step | `role responsible_for activity`, or `org_unit responsible_for activity` |
| Who holds a role | `agent implements role`, dated with `valid_from`/`valid_to` |
| Who belongs to a team or department | `agent part_of org_unit`, dated when the source says |
| A step reads a document or record | `activity reads_from data` |
| A step is done in a system | `activity uses component` |
| A policy ("only approved invoices may be paid") | promote a `normative` claim `about` the activity |
| What happens in practice, against the policy | promote a `descriptive` claim, `contradicts` the policy |

Rules this pack adds:

- `bpm.approver_cardinality`: a bpm role has one holder at a time. When a source says
  someone took over, close the previous holder's window in the same payload. When two
  sources disagree about the holder, both edges are kept and shown as contested.
- `bpm.flows_between_steps`: `flows_to` connects activities and gateways, never roles.
- `bpm.approval_needs_report`: `approved_by` needs basis reported or observed.

The ontology is applied by `sql/10_ontology.sql` (there is no pack installer in Phase 1).

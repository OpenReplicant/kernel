# {{paper.title}}

> **Toggle id:** `{{paper.id}}` · **Fidelity:** {{fidelity.overall}} · **Schema:** 0.1
> {{paper.authors}} ({{paper.year}}{{#paper.venue}}, {{paper.venue}}{{/paper.venue}}) · arXiv {{paper.arxiv}}

<!--
Human-readable blueprint generated from spec.yaml. spec.yaml is the source of
truth; regenerate this file rather than editing it by hand. Every value the
paper does not state outright is tagged with its provenance:
  [stated] paper or appendix · [repo] published code only
  [inferred] compiler's reading · [defaulted] platform default
-->

## 1. Mechanism

{{mechanism.summary}}

**Claims under test**

{{#mechanism.claims}}
- {{.}}
{{/mechanism.claims}}

## 2. Sources read

| Kind | Reference | Commit | License |
|---|---|---|---|
{{#paper.sources}}
| {{kind}} | {{ref}} | {{commit}} | {{license}} |
{{/paper.sources}}

## 3. Primitive map

Which slots this paper fills, at which scope, and where each implementation
comes from. Resolution order is **registry → paper code → generated**.

| Slot id | Primitive | Scope | Mode | Implementation | Source | Provenance |
|---|---|---|---|---|---|---|
{{#slots}}
| `{{id}}` | {{slot}}{{#subtype}} ({{subtype}}){{/subtype}} | {{scope}} | {{mode}} | {{implementation.kind}}: `{{implementation.ref}}` | {{implementation.source}} | [{{provenance}}] {{source_ref}} |
{{/slots}}

### 3.1 Parameters

| Slot | Parameter | Value | Provenance | Source | Sweep |
|---|---|---|---|---|---|
{{#slots}}{{#params}}
| `{{slot_id}}` | {{name}} | {{value}} | [{{provenance}}] | {{source_ref}} | {{sweep}} |
{{/params}}{{/slots}}

### 3.2 Prompts

Prompts are copied verbatim into `prompts/` with a header comment citing their
origin. Paraphrased or reconstructed prompts are marked `[inferred]`.

| Prompt | File | Origin | Provenance |
|---|---|---|---|
{{#slots}}{{#prompts}}
| {{id}} | `{{file}}` | {{source_ref}} | [{{provenance}}] |
{{/prompts}}{{/slots}}

## 4. Wiring

How slot events flow. Compiles to connections in `chains/main.json`.

```
{{#wiring}}
{{from}}  ──▶  {{to}}{{#condition}}   when {{condition}}{{/condition}}
{{/wiring}}
```

## 5. Assets

### 5.1 Stores and models

| Asset | Kind | Requirements | Provenance |
|---|---|---|---|
{{#assets}}{{^mcp}}{{^environment}}{{^external}}
| `{{id}}` | {{kind}} | {{requirements}} | [{{provenance}}] |
{{/external}}{{/environment}}{{/mcp}}{{/assets}}

### 5.2 MCP servers

Only behaviour **the model itself calls as a tool** is exposed over MCP.
Internal primitives (evaluators, reflectors, controllers) run as RuleGo nodes.

| Server | Strategy | Ref / commit | License | Transport | Tools |
|---|---|---|---|---|---|
{{#assets}}{{#mcp}}
| `{{id}}` | {{strategy}} | {{ref}} @ {{commit}} | {{license}} | {{transport}} | {{tools}} |
{{/mcp}}{{/assets}}

### 5.3 Environments

| Environment | Driver | Image | Lifecycle | Capabilities | Snapshot | Network | Resources |
|---|---|---|---|---|---|---|---|
{{#assets}}{{#environment}}
| `{{id}}` | {{driver}} | `{{image}}` | {{lifecycle}} | {{capabilities}} | {{snapshot_method}} | {{network}} | {{resources}} |
{{/environment}}{{/assets}}

### 5.4 External services

| Service | Purpose | Auth | Determinism | Rate limit | Cost/call |
|---|---|---|---|---|---|
{{#assets}}{{#external}}
| `{{id}}` | {{purpose}} | {{auth.method}} ({{auth.secret_ref}}) | {{determinism}} | {{rate_limit}} | {{cost_per_call_usd}} |
{{/external}}{{/assets}}

## 6. Models

| Role | Paper used | This run uses | Params | Provenance |
|---|---|---|---|---|
{{#models}}
| {{role}} | {{paper_model}} | {{use_model}} | {{params}} | [{{provenance}}] |
{{/models}}

Model substitution is the most common reason absolute scores diverge from the
paper. Acceptance in §8 is set with that in mind.

## 7. Behavior tests

Mechanism checks over execution traces. These must pass before any benchmark
number is reported.

| Test | Claim | Given | Within | Expect |
|---|---|---|---|---|
{{#behavior_tests}}
| `{{id}}` | {{claim}} | {{given}} | {{within}} | {{#negate}}NOT {{/negate}}{{expect}} |
{{/behavior_tests}}

## 8. Evaluation

- **Benchmark:** {{evaluation.benchmark}} ({{evaluation.split}}{{#evaluation.subset}}; subset: {{evaluation.subset}}{{/evaluation.subset}})
- **Harness:** `{{evaluation.harness_asset}}`
- **Metrics:** {{evaluation.metrics}}
- **Baselines run alongside:** {{evaluation.baselines}}
- **Acceptance:** {{evaluation.acceptance.mode}} {{evaluation.acceptance.value}} — {{evaluation.acceptance.note}}

| Metric | Paper value | Condition | Source |
|---|---|---|---|
{{#evaluation.paper_results}}
| {{metric}} | {{value}} | {{condition}} | {{source_ref}} |
{{/evaluation.paper_results}}

## 9. Fidelity gaps

Where this implementation knowingly departs from the paper, and how much each
departure is expected to matter. Read this before interpreting REPORT.md.

| Where | Why | Expected impact |
|---|---|---|
{{#fidelity.gaps}}
| `{{where}}` | {{reason}} | {{expected_impact}} |
{{/fidelity.gaps}}

**Unverified fields:** {{count_inferred}} inferred, {{count_defaulted}} defaulted.
When a reproduction misses, these are the first suspects.

## 10. Composition

- **Exclusive slots claimed:** {{exclusive_slots}} (derived from the slot registry)
- **Can wrap:** {{compat.wraps}}
- **Conflicts with:** {{compat.conflicts}}
- **Requires:** {{compat.requires}}

{{compat.notes}}

## 11. Folder contents

```
{{paper.id}}/
├── SPEC.md            this file
├── spec.yaml          source of truth
├── chains/            RuleGo rule chains (main.json + sub-chains)
├── components/        new primitives not found in the registry
├── prompts/           verbatim prompts with origin headers
├── assets/            compose / k8s manifests, MCP server wrappers
├── bench/             benchmark env + run script
├── tests/
│   ├── contract/      components honor slot interfaces
│   ├── behavior/      trace assertions from §7
│   └── smoke/         one tiny end-to-end task
└── REPORT.md          reproduction result vs §8
```

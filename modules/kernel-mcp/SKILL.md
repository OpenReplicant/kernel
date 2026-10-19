---
name: knowledge-kernel
description: Use when reading from or writing to the Knowledge Kernel through its MCP tools — recalling what is known and who said it, remembering a fact or decision, seeing what changed, explaining where a claim came from, or (operator tools) recording claims as a change set.
---

# Working with the Knowledge Kernel

The kernel stores **claims**: attributed, referenced statements about entities, each with a belief score.
You can read anything your role is cleared for. Which tools you see depends on your login's profile:
`memory` (four tools, below) or `operator` (every tool).

## Memory (the memory profile)

- `recall(question, budget?)` → a briefing: one line per memory or claim, saying who said it (you, a
  person, another agent) and its belief from 0 to 1, ending in a handle such as `#1a2b3c4d`.
- `remember(kind, text, about?, replaces?, because?)` → records one sentence as yours. `kind` is
  `fact`, `decision`, `preference`, `commitment`, `correction` or `conclusion`; `about` is what it
  concerns (a name, an IRI or a #handle); `replaces` is the #handle of a memory this one corrects (the
  old one is kept, marked superseded); `because` lists the #handles it follows from. A conclusion needs
  `because`: it is recorded as inferred from those claims, kept as private as the most private of them,
  and flagged when one of them is withdrawn. When you work something out from what you recalled,
  record it as a conclusion so the next agent starts from it.
- `remember(..., when={...})` adds a recall cue, for what must come back whatever the ranking:
  `{"always": true}` (a standing instruction or preference, at every session start),
  `{"mentions": ["Sam"]}` (when a prompt names it), `{"on": "--05-30", "before": 7}` (a date, yearly with
  `--`), `{"where": "deploy/*"}` (working on those files), `{"tool": "Bash(git push*)"}` (before that
  action). Lines a cue brings up say which cue fired. It shows in `recall` once the kernel has applied it, usually within a second.
- `explain(handle)` → where a line came from: the source and quote, status history, beliefs.
- `changes_since(since?)` → what others added, superseded or retracted since you last looked, and
  the cursor to pass next time.

You can also write a line in your reply that starts with `Decision:` or `Remember:` (or `Preference:`,
`Commitment:`, `Correction:`); when the session transcript is recorded, the line becomes a memory
quoting it. Remember what someone would want later: decisions and their reasons, what a person told
you, findings about code or systems. Not plans, guesses or restatements.

**Cite the #handles you rely on, with their belief.** A line said by "you" or another agent is a
proposal, not a decision by a person: say which it is.

## Reading (operator tools)

- `search(text)` → claims ranked by relevance to your words (full text over values and IRIs, then
  belief); substring match on labels and IRIs when no word matches.
- `get_entity(iri)` → the entity card: current claims with status and belief.
- `query_claims(filters)` → claims by subject, predicate, object or scope (IRIs or CURIEs); deprecated claims only on request.
- `explain(claim_id)` (a claim id or a #handle) → sources and quotes, what it was derived from, what uses it, status history,
  beliefs and verifications. Use this before relying on a claim you didn't make.
- `list_slots()`, `list_modules()` → the platform's own self-model.
- `list_runs(plan_step?, used?, module?)` → runs: who did what, for which plan step, using which claims,
  with status and outputs (an actuator records what it did this way, never as claims).

If the harness runs the kernel hooks, claims related to each prompt arrive in your context as lines
`subject predicate object [claim <id>, belief <b>]`. They are recall, not proof: cite them by id with
their belief, and `explain` the ones you rely on.

When you answer a question from the kernel, **cite claim ids and show their belief** (invariant 6).
Deprecated claims are history, not current fact; say so if you mention them.

## Writing (operator tools)

1. `start_run(module, plan_step?)` → run id. Every change set names the run that produced it. If the
   session-start hook gave you a session run, use that one instead (it is finished when the session
   ends).
2. Build the change set (schema: `kernel/schemas/change_set.schema.json`):
   - `idempotency_key`: stable, e.g. `<your module>:<source sha256>:<n>`; resubmitting is safe.
   - `attributed_to`: your own agent IRI. Attribution to anyone else is rejected.
   - `target_scope`: a scope you have write clearance for.
   - `ops`: `add_claim`, `set_status`, `add_belief`, `add_verification`, `add_entity`, `add_artifact`.
3. `submit_change_set(cs)` → `{id, status, lane}`; `get_change_set(id)` shows its status (and the
   validation report if it was found invalid).
4. `finish_run(run_id, status, cost)` for runs you started yourself.

**What to record:** facts you established in this session that someone would want later (decisions,
findings about code or systems, what a person told you), each with its source: the file and lines
(`deterministic` or `extracted` with the quote), the person (`testimony`), or the claims you reasoned
from (`inferred`). Not your plans, guesses or restatements of claims already in the kernel.

Rules the kernel enforces (your change set is marked invalid otherwise):
- Every claim has a reference (`source_uri` or `artifact`) or `derived_from` claims.
- `evidence_type: extracted` claims include the exact supporting `quote`.
- `evidence_type: inferred` claims list their premises in `derived_from`, and a derived claim cannot be
  placed in a scope readable by more people than its inputs.
- Predicates must be approved properties. Before proposing a new one, look for a term that already
  exists: first in the installed packs (`search` or `query_claims` on `rdf:type owl:ObjectProperty`),
  then in the catalogs listed under `catalogs:` in `ontology/packs.yaml` (LOV, W3C Recommendations,
  schema.org, OBO Foundry, BioPortal). Only if none fits, submit `rdf:type owl:ObjectProperty` (or
  similar) for it with the vocabulary it comes from — that goes to the review lane automatically.
- Processes use BBO (`https://www.irit.fr/recherches/MELODI/ontologies/BBO#`: `has_nextFlowNode`,
  `has_container`, `Task`, gateways); observations use SOSA (`http://www.w3.org/ns/sosa/`): one
  `observed` claim per feature, property and window — subject the feature of interest, predicate
  `sosa:hasSimpleResult`, qualifier `sosa:observedProperty`, `valid_from`/`valid_to` the window. These
  two have no kernel prefix: write full IRIs.
- You cannot verify claims made by your own module or agent.
- Nothing is deleted: use `set_status` with `deprecated` + `reason` (`superseded` with `replaced_by`,
  `refuted`, or `retracted`). Use `expect_status` so you don't overwrite someone else's change.
- Conditions go in `qualifiers` (keys are property IRIs), never inside the value.

Belief values come from belief-model modules, not from you. Don't submit `add_belief` unless you are one.

## What to expect

- **Status after submitting:** `submitted` → the validator → `applied`, or `invalid` (read
  `get_change_set(id).validation_report`: JSON Schema errors or SHACL shape messages), or
  `awaiting_review` when you type a property, class, module, chain or mapping. A person approves
  those; don't resubmit while you wait. The same `idempotency_key` always returns the same change set.
- **Evidence type:** `deterministic` for what you parsed from code, config or schemas (reference the
  file, ideally with a span); `extracted` for what you read in prose (include the exact `quote`);
  `inferred` for conclusions (list `derived_from`); `observed` for runtime observations; `testimony`
  for what a person told you or what you assert yourself.
- **Scopes:** `urn:kk:scope:default` for working claims, `urn:kk:scope:vocab` for terms and
  self-model descriptions. Your token only shows and accepts what your role is cleared for.
- **Belief** (`belief-corroboration-v0`): independent sources per statement ÷ (sources + conflicts + 1).
  Two claims citing the same source, or made by the same agent, count once; a different value for a
  single-valued property in an overlapping validity window is a conflict; non-overlapping
  `valid_from`/`valid_to` windows are changes over time. Cite a different, real source to
  corroborate; repeating a claim does not raise its belief.
- CURIEs (`kk:fills`, `rdfs:label`, `dcterms:license` …) are accepted wherever IRIs are.

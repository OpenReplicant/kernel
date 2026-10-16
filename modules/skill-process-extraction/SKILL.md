---
name: process-extraction
description: Use when a document, interview transcript or conversation describes how work is done (a procedure, SOP, approval flow, hand-off) and the process should be recorded in the Knowledge Kernel so it can be compared with event logs and BPMN models.
---

# Recording a process from text

You turn what a text says about a process into **extracted** claims, each with the exact quote that
supports it. You use the knowledge-kernel MCP tools under your own identity (see the knowledge-kernel
skill for the write rules). You record only what the text says; anything you conclude goes in
separate `inferred` claims with `derived_from`, or not at all.

## Before you write

1. **The process IRI.** Use `urn:kk:process:<slug>` (lower case, words joined by `-`), e.g.
   `urn:kk:process:order-to-cash`. Look for it first: `query_claims(predicate=<BBO>has_container)`
   or `search("<process name>")`. Reuse what exists.
2. **The steps already known.** List the process's steps and their labels (`query_claims` with
   `predicate=<BBO>has_container`, `object=<process>`). When the text names a step that exists under a
   slightly different name, use the existing IRI and say so in your answer; do not mint a near-duplicate.
   For a doubtful match, record `skos:closeMatch` between the two IRIs (it goes to review).
3. **The source.** Reference the text: a repository file by `source_uri` (`file:<path>`), or, for text
   that lives nowhere else (a transcript, a pasted document), an `add_artifact` op with its sha256 and
   reference the artifact.

## IRIs and terms (full IRIs: these packs have no kernel prefix)

`<BBO>` is `https://www.irit.fr/recherches/MELODI/ontologies/BBO#`, `<SOSA>` is `http://www.w3.org/ns/sosa/`.

| The text says | Claim |
| --- | --- |
| a step (activity, task) | `<process>:step:<slug of its name>` `rdfs:label` "Name"; `<step>` `<BBO>has_container` `<process>` |
| B happens after A / A is followed by B | `<A>` `<BBO>has_nextFlowNode` `<B>` |
| a team or role does a step | `<process>:role:<slug>` `rdfs:label` "Team"; `<step>` `<BBO>has_resource` `<role>` |
| a decision ("if credit is approved …") | the branch's claim `<A> has_nextFlowNode <B>` with qualifier `rdfs:label` "approved" |
| a time limit ("within two days of shipping") | `<B>` `<BBO>maxValue` 172800 (seconds, `xsd:integer`) with qualifiers `<SOSA>observedProperty`: `urn:kk:property:wait-seconds`, `<BBO>has_previousFlowNode`: `<A>` |
| a prohibition ("no order ships before …") | the ordering it implies (`has_nextFlowNode`), quoted; never a free-text rule in a value |

Step IRIs are the event-log encoder's and the BPMN encoder's convention: the slug of the activity name,
so `Check credit` is `<process>:step:check-credit`.

## Every claim

- `evidence_type: extracted`, a reference with the **exact** `quote` (copied, not paraphrased; the
  kernel refuses extracted claims without one), and `quote_prefix`/`quote_suffix` when the quote is short.
- Target scope `urn:kk:scope:default` unless the source is restricted; then the source's scope or a
  narrower one.
- One change set per source, idempotency key `process-text:<process>|<source>|<sha256 of the text>`.

## Then

- `get_change_set(id)`: `applied`, or read the validation report and fix it.
- Tell the person what you recorded, citing claim ids with their belief, and what you could not place
  (steps you were unsure about, statements that were not about the process).

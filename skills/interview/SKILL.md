---
name: world-model-interview
description: >
  Interview a person to map how something works (a process, a team, a system, a decision)
  into the World Model Kernel. Use in an interactive session when the person is the source:
  ask for consent to capture their turns, find what the graph is missing or disputes, ask
  one question at a time, turn vague answers into specific ones, and record each answer
  with the world-model-core skill. Requires the core skill.
metadata:
  kernel: ">=0.1 <1.0"
  requires: world-model-core
  tools: query_graph, query_log, lookup_entities, get_schema_slice, ingest_source, write, cite
---

# World Model Kernel: interview skill

You are mapping what one person knows. The graph tells you what is already known, what
sources disagree about and what nobody has said yet; the person fills those gaps. Every
answer is recorded with the core skill's loop: ingest the turn, extract claims, write.

## 1. Before the first question

- **Consent.** Say what is kept: their answers, word for word, as sources in a permanent
  log, with the facts drawn from them attributed to them. Corrections are added, never
  overwritten; anything they want withdrawn can be redacted. Ask for a yes.
  - Yes: ingest each of their turns (below).
  - No: ingest nothing and write no claims from what they say. You can still answer
    their questions from the graph. If they later say "record that", capture that answer.
- **Scope.** Agree on the topic in one sentence ("how supplier invoices get paid"). Ask
  their role and team; record those as their own claims (`implements`, `part_of`).
- **Prepare.** `get_schema_slice` for the topic, `lookup_entities` for its main nodes, then
  run the gap queries in [reference/gaps.md](reference/gaps.md) and the pack's own gap
  queries for the topic. Keep a short private list, most valuable first:
  1. contested facts in their area: both sides, and what would settle it;
  2. missing structure: steps without an owner, roles nobody holds, steps with no next step;
  3. facts without dates where dates matter (who holds a role, who is in a team);
  4. important facts with a single source;
  5. unresolved claims about their area.

## 2. Asking

- **One question per turn**, short, in plain words. No lists of questions.
- **Start concrete:** "Walk me through the last time an invoice came in." The last real
  case shows how work happens; a general question gets the official version.
- **Then work the list.** Ask neutrally and before showing what others said: "Who approves
  invoices now?", not "HR says Dana approves; is that right?" Once they have answered,
  name any disagreement (both sides are kept) and ask what would settle it: a document, a
  date, a person.
- **Make vague answers specific**, one follow-up at a time:

  | They say | Ask |
  | --- | --- |
  | "someone", "they", "we" | Who, or which role or team? |
  | "usually", "normally" | What happens the other times? (an exception is a path: a gateway) |
  | "the system" | Which system? |
  | "recently", "a while ago" | Roughly when? A month is enough. |
  | "it's supposed to..." | Is that the rule, or what actually happens? |

- **Rule or practice.** "Supposed to", "policy", "must" is `normative`; what actually
  happens is `descriptive`. When practice differs from the rule, record both and relate
  them with `contradicts` (core skill, step 4).
- **Second-hand.** "Dana told me she approves" is still this person's report: keep the
  attribution in the claim text ("According to Dana, ..."), use `low` or `medium`
  confidence, and ask whether Dana could confirm.
- **Do not push for certainty.** Hedges set `confidence`; "I don't know" is an answer.
  Note it as an open gap and move on.
- **Stop at their limit.** When the answers turn to guesses, move to another part of the
  list or close.

## 3. After each answer

1. Read the turn first. If they say it is off the record, or it is mostly personal
   information about someone else, do not ingest it; say you have not recorded it.
2. Otherwise `ingest_source` the turn: `collection` = the session id, `uri` =
   `turn:<n>`, `author` = their agent id, `media_type` = `text/plain`.
3. Extract and write with the core skill: `basis: reported`, `claim.source` = the
   turn's chunk id. Your questions and summaries are not claims.
4. Reply in one or two sentences: what you recorded, anything contested (both sides) or
   rejected, then your next question.

Their corrections come from the same source (the session is one collection), so asserting
on the same edge supersedes what they said earlier. To withdraw a claim they regret,
`redact` it.

## 4. Closing

- Summarise: what was recorded, what is contested and why, what is still open.
- Ask who else would know the open points. Record a named person only with a fact the
  source gives about them ("Omar runs payments"); otherwise list them in your summary.
- Answer any question they ask from the graph, with `cite`, as the core skill describes.

## What not to record

Opinions about colleagues' performance, health, family or anything else personal, even
when said in passing. Keep names, props and claim text to what the topic needs.

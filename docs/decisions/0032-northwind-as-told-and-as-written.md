# 0032. Northwind as told and as written, and comparing the views

Date: 2026-10-06 · Status: accepted (completes ADR 0027's three views for the demo)

## Context

ADR 0027 built the view as done: Northwind's purchase-request log, read by `wmk-process`.
It left out the SOP and the interviews that the demo needs. The point of three views is
their disagreement: "where the views disagree, belief shows it as contested: that is the
discovery deliverable". Three things are missing:

- **The views themselves.** The extractor that will read documents and transcripts
  (ADR 0024) is not built, and CI cannot call a model.
- **Interviews name people.** Their turns must be sealed, and the speakers must be agents
  before the turns can name them (ADR 0022).
- **A way to read the disagreement.** Belief says that a flow is contested, not which view
  says what.

## Decision

**The views as told and as written are a scripted fixture, `northwind-views`.** Its script
is the tool calls an extractor following the core, process and interview skills makes:

- reported claims;
- each quoting its source, with the quotes checked by the kernel;
- an expected graph written by hand.

It stands in for the extractor in CI and in the demo's scripted form. The extractor will
later run over the same sources and be scored against the same expected graph.

- **As written:** SOP-FIN-007, the purchase-request procedure (version 3, March 2026),
  origin `org:northwind-finance`. It still has a budget check that nobody does, and its
  targets are normative claims.
- **As told:** two interviews, each a session whose collection is an opaque id:
  - Maya Chen, procurement lead, on 15 September;
  - Lucia Ferreira, finance controller since 1 August, on 16 September.

  Each answer is a source written by the speaker (`author`), so the speaker is its subject
  and it is sealed. Each speaker's agent is created first, by an observed claim that the
  interview took place. Agents are keyed by email, so the same person can sign in to the
  explorer later.
- **Rule and practice:**
  - A flow with `when` is a routing rule: every case for which the condition holds
    continues at its end (ADR 0027).
  - Someone who says that cases skip a step denies that rule. Someone who says the rule
    holds asserts it, with `valid_from` when they can vouch only from a date.
  - The rule as policy is a normative claim that asserts no flow, since belief counts
    every modality. A practice that breaks the rule `contradicts` the policy claim.

**The fixtures assemble with resolution, not in a script.** A script creates its nodes and
cannot reuse another fixture's. `make seed` plays the views but no longer the log fixture
(`seed: false`). `make northwind` assembles Northwind in a running stack:

1. play `northwind-views`, unless `make seed` already has;
2. map the log with `wmk-process discover`, which reuses the views' nodes by name;
3. check the views against the log with `conform`;
4. print the comparison.

The fixture player now renders the source specs too, so a turn's `author` can name an agent
created earlier in the script.

**`wmk-process compare` reads the disagreement.**
- **Which edges:** each step's `part_of` the process and each flow, read as `conform` reads
  them.
- **Which views:** the configuration names the collections of each view (`views:` in the
  YAML). The view as done defaults to the log's and the conformance digest's collections.
- **Each source's stance:**
  - It replays the log entries citing that collection, in order.
  - The latest assertion on an edge is that source's stance, as belief counts it.
  - A retraction by a later run reads as the source's denial, since that is how belief
    counts it.
- **The report:** each step and flow with the kernel's belief and each view's stance:
  asserts (with its window, if any), denies, divided (its sources disagree) or silent. It
  groups them:
  - contested: some source asserts and some denies;
  - denied: some source denies and none asserts;
  - stated by one view only;
  - agreed by two or more views;
  - stated by none of these views (only by other sources), listed when there are any.
- **Reads only.** It writes nothing, like `forge audit` (ADR 0031). It reads operations,
  never claim text, so it prints nothing sealed.

## Consequences

The demo can open with the discovery deliverable, honestly:

- **Who approves large requests.** The SOP and Lucia assert that every request over 10,000
  euros goes to the controller first. Maya and the log deny it.
- **What the log adds.** It names the six requests that skipped the controller. Five
  predate 1 August. The sixth, PR-1092, was ordered on 2 August, the day after Lucia
  started, and approved on 6 August. Whether it was an exception or a request caught in the
  handover is a question for the next interview: the log alone cannot settle it.
- **The budget check:** written, denied by Lucia, and never seen in the log.
- **Late approvals:** told by Maya and seen in the log, written nowhere.

In CI, `make northwind` runs after `make seed`, and replay covers the result.

Limits:
- Conformance is checked over the whole log, not per window. A rule that held from a date
  and failed before it is denied as a whole, with example cases in the digest.
- The scripted extraction measures the plumbing, not extraction quality (CLAUDE.md,
  Testing). The extractor's own eval will use these sources.
- Only steps and flows are compared, not roles, systems or targets.

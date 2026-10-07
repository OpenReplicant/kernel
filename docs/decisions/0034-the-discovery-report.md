# 0034. The discovery report

Date: 2026-10-06 · Status: accepted

## Context

ADR 0033's build order puts the discovery report second, after ranking. The kernel can
already say two things about a mapped process, as two terminal listings:
- where its views disagree (`wmk-process compare`, ADR 0032);
- what to automate first (`wmk-process rank`, ADR 0033).

A client needs one document: the process as mapped, where its views disagree, what the log
measures, and the ranking. Each sentence must show what it rests on, so that a reader can
check any line without the kernel. That means the claims behind the sentence and the words
of their sources.

`compare` reads operations and never claim text, so it prints nothing sealed. A report that
quotes its sources has to read the words, interviews included.

## Decision

**`wmk-process report` writes the discovery report as Markdown, or as JSON with `--json`.**
- **How it reads:** like `compare` and `rank`, through the gateway, with the process read
  once at one offset. It writes nothing.
- **Its four sections:**
  1. **The process as mapped:** each step in flow order, starting from the steps no flow
     leads to. Each step has its belief and the views that state it, deny it, or are divided
     on it. Then each flow out of the step, the same way.
  2. **Where the views disagree:** the facts `compare` calls contested, denied or stated by
     one view only. Each fact lists every source's stance on its own line: it states the
     fact (with its window, if any) or denies it.
  3. **Measures:** the process's cases and median cycle time, then each step's executions,
     repeats, time before it and handoffs. These are the KPI values `rank` uses.
  4. **What to automate first:** the ranking with its factors and weights, plus:
     - what to settle first;
     - the steps with nothing measured;
     - the decisions already written as rules;
     - `rank`'s note that a score is not feasibility.

**Each sentence cites the claims it rests on.**
- **A sentence about a fact** (a step, a flow, a measure, a system) cites one claim per
  collection of the views read: the claim of that collection's latest assertion on the
  fact's edge. That is the assertion belief counts (ADR 0021). A test checks every citation
  against `kernel.counted_assertions`.
- **Footnotes:** the citations are Markdown footnotes, numbered in order of first use. Each
  footnote gives:
  - the view and the collection;
  - the claim's basis and log offset;
  - the words of the source that the claim quotes. An observed claim quotes nothing, so it
    gives its own text instead. For the log, that is the digest line's numbers and example
    cases.
- **Sentences that state no fact** cite nothing: the views read, the weights, a step with
  nothing measured, the closing note.
- **The JSON:** each sentence with the edge ids and claim ids it rests on, which is the
  shape `cite` takes. A harness that shows the report to someone can record what they were
  shown. The report itself does not call `cite`, since it writes nothing. Recording what a
  reader was shown belongs to the recall ADR.

**The words of sealed sources.**
- The report prints what the gateway's reader can read, so interview turns appear opened,
  as in the explorer.
- It attributes each claim to its view and collection, never to its author. Collections
  hold no names (ADR 0022).
- Once a person is erased, their words read [erased] in every later report. Their stance
  still counts, as belief counts it.
- A report saved to a file or sent to a client is a copy that erasure cannot reach.
  Whoever keeps one holds the personal data in it.

**What the report leaves to people.** The report gives the facts, their sources and the
scores. Conclusions, recommendations and the questions for the next interview are for the
consultant to write.

**`make northwind` prints the report** in place of the comparison and the ranking, which
it contains. `compare` and `rank` remain for the terminal and for their own JSON.

## Consequences

**Northwind's report** has 54 footnotes.
- **The approval rule:** the sentence on it cites four sources, each in its own words:
  - the SOP;
  - the procurement lead's interview, which denies the rule;
  - the controller's interview, which states it from 1 August;
  - the conformance digest, which counts six requests that skipped the controller and
    names three of them.
- **The budget check** cites the SOP, the controller's denial and the conformance digest,
  under which no case ran it.

**A fixture bug the report found.** Its first run quoted the SOP as "Requests over 10".
- **Cause:** in the `northwind-views` script, an unquoted YAML flow scalar ended at the
  comma in "10,000". The gateway dropped the rest as an unknown claim key, and the kernel
  checked a 16-character quote.
- **Fix:** the two claims affected are quoted now.
- **Guard:** the fixture player now fails a write whose claim has keys the gateway does not
  know.

**What changes elsewhere:**
- `compare` keeps the claim behind each stance, and can follow edges beyond the facts it
  compares, such as the measures a ranking used.
- `compare` and `rank` each accept a model already read, so the report reads it once.
- `discover` names the process's KPIs with templates, as it already did for the step KPIs.

**Limits:**
- **What it reports:** only steps, flows, measures and systems. Roles, data, targets and
  policies are left out, so the SOP's three-day target is not set against the measured
  wait.
- **Which sources:** footnotes cite only the views read. A source outside them is not
  cited, even when belief counts it.
- **Format:** Markdown only. Turning it into a page or a PDF is a job for any Markdown tool.

# 0033. Ranking what to automate, and the order of the next steps

Date: 2026-10-06 · Status: accepted

## Context

ADR 0026's loop has five steps: map the company, rank what to automate, automate it,
observe what runs, and propose changes. Mapping is built: the view as done (ADR 0027), the
views as told and as written, and their comparison (ADR 0032). Nothing yet says what to
automate first.

The ranking is also the second half of a discovery deliverable. A client who has seen where
the views disagree asks next which steps to automate. The answer must be computed from what
the kernel holds, as conformance and belief are, and show its inputs, so that each score
can be checked.

The kernel holds each step's place in the process, its flows and their conditions, belief,
the roles and systems involved, and three KPIs: cases, median cycle time and executions per
step. That does not say where time goes or where work is redone.

## Decision

**`discover` measures each step more.** Four more KPIs per step, computed from the log
like the others:

| KPI | What it measures | Unit |
| --- | --- | --- |
| Repeats of *step* | executions beyond the first in a case: rework | executions |
| Median time before *step* | from the case's previous event to the step | hours |
| Total time before *step* | the same, summed over the period | hours |
| Handoffs into *step* | executions whose previous event was another role's | handoffs |

- **Recording:** they are written like the other KPIs, as `measures` edges over the log's
  period with the value in props. The step's line of the digest states them, and the
  step's claim cites that line.
- **When a KPI is left out:**
  - A step that never follows another event gets no time or handoff KPIs.
  - Handoffs need a role on both events.
- **What time before a step covers:** logs with only completion times cannot separate
  waiting for a step from doing it.
- **Names:** the KPI names are fixed templates (`discover.KPIS`), and `rank` reads the KPIs
  by them.

**`wmk-process rank` scores the steps on what the kernel holds.** Like `compare`, it reads
through the gateway at one offset and writes nothing:
- the process as `conform` reads it;
- the `measures` edges into its steps;
- the systems its steps are performed in.

Each step with executions measured gets six factors from 0 to 1:

| Factor | Value |
| --- | --- |
| volume | its executions over the most executions of any step |
| waiting | its total time before over the largest such total |
| rework | its repeats over its executions |
| handoffs | its handoffs over its executions |
| rule | 1 when a routing rule into it (a flow with `when`) is contested or rejected |
| system | 1 when it is performed in a system |

- **The rule factor:** a rule some source states and some source denies is a rule practice
  breaks, and a runtime would enforce it.
- **The system factor:** a step performed in a system is one a runtime can drive.
- **The score** is the sum of each factor times its weight.
  - The weights default to 1. A process's configuration sets them under `rank.weights`, and
    every ranking prints the weights it used.
  - Ties go by name.
- **Settle first:** each step also lists the contested facts about it, meaning its place in
  the process and its flows. They do not change the score. The score says where automation
  would pay; the contested facts say what people must agree on before anyone automates the
  step.
- **Which KPI value:** where a KPI has several values, `rank` uses the latest period's and
  never a retracted one.
- **Listed apart:**
  - steps with no executions measured;
  - decisions already written as rules, meaning steps whose flows out all carry conditions,
    with each branch's belief. A runtime can run these as they are.
- **Output:** the text gives every input next to its factor. The JSON adds each input's
  edge id, belief and window, and the discovery report will read the JSON.

**The ranking is not written to the kernel.**
- It is a view over facts at one offset, and it is quick to compute again.
- Writing it would make a score look like a fact about the world, while the weights are
  the choice of whoever ranks.
- Choosing what to automate is a person's decision. That decision enters the kernel as a
  proposal and its approval (ADR 0019, 0029), with the workflow adapter.

**A score is not feasibility.** The score cannot tell whether a step can be automated
(judgement, exceptions, regulation). The people who run the step judge that, or a model
reading what they said. The ranking says so in its output.

**The order of the next steps.** The discovery deliverables come before any runtime. The
open demo and the first engagements need them first, and the workflow adapter builds on
them.
1. Ranking (this ADR).
2. The discovery report: the map, the disagreements, the measures and the ranking, with the
   sources of every sentence.
3. Intake from a client's folder: documents, transcripts and exports go in through the
   parsers, the extractor and the adapters.
4. The first workflow adapter, for Operaton (ADR 0026, item 4).
5. `make demo` and the write-up.

CLAUDE.md records this order.

## Consequences

**Northwind's ranking:**

| Rank | Step | Score |
| --- | --- | --- |
| 1 | Review purchase request | 4.12 |
| 2 | Approve purchase request | 3.84 |
| 3 | Create purchase order | 3.47 |

- **Review** is where most time goes: 137 executions and 3,418 hours since the previous
  event. 17 reviews are repeats after a revision.
- **Approve** is slow (a median of 52 hours), and the rule that routes large requests to it
  is contested. Settling that rule comes first. On the log alone the approval ranks third,
  at 2.84.
- **The budget check** is listed as not measured. The SOP writes it down; the controller
  and the log deny it.

**What changes elsewhere:**
- `discover` writes 25 more KPIs for Northwind's log, and the log fixture is refreshed.
- `make northwind` prints the ranking after the comparison.

**Limits:**
- Time before a step is measured between completions. Logs with start events could
  separate waiting from working; today the reader drops them.
- Costs are not in the model: hourly rates, error costs, the value of a faster case. Volume
  and time stand in for them.
- Factors are relative to one process, so scores do not compare across processes.
- The rule factor marks a contested or rejected rule, not how often practice breaks it. The
  count is in the conformance claim, which the report quotes.

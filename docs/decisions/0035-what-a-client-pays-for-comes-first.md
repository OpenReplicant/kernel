# 0035. What a client pays for comes first

Date: 2026-10-07 · Status: accepted (direction; amends the order of ADR 0026 and ADR 0033;
each item below gets its own ADR before code)

## Context

ADR 0026 put reputation first. One synthetic company, Northwind, would go around the whole
loop as an open demo, and the demo would bring the client work.

Half of that loop is built:
- mapping a process from three views, and comparing them (ADR 0027, 0032);
- ranking what to automate (ADR 0033);
- the discovery report, each sentence with its sources (ADR 0034).

Also built:
- the forge audit of changes without approval (ADR 0031);
- drift between what is declared and what runs, on Docker (ADR 0028);
- the approval channel (ADR 0029, 0030).

The rest of the loop is the Operaton adapter, an automation running on it, and the demo.
None of it earns anything until a client pays for a pilot, and that client may already run
a workflow runtime of its own.

The owner has asked to steer toward what can earn money now. What a client pays for soonest
is a document at the end of a fixed-scope engagement. It is made from what the client can
hand over in a week: documents, interview transcripts and exports. Three such documents are
close to what exists:
- **The discovery report.** Built, but only the scripted Northwind fixture feeds it.
  Missing: a way in for a client's folder.
- **A controls test.** A client's written rules (approval above a threshold, a review before
  an order) are tested against every case in an export, and every exception is listed.
  - `conform` already counts the cases that broke a routing rule, but names only three of
    them. A controller or an auditor needs each one, with its date and the values the rule
    read.
  - The test needs no model. The export is read deterministically, and a person writes the
    rules from the policy.
- **Change-approval evidence.** The forge audit over an audit period, as a document with
  receipts, for SOC 2 or ISO 27001 change management. Missing: the period and the
  document.

The three have one form: findings that each cite the claims and the source words they rest
on (ADR 0034). A reader can check any finding line by line without the kernel. Audits and
assessments are where someone does check.

**Not ready:** the extractor (ADR 0024) is still a proposal, and bulk extraction waits for
the annotated eval's floors. So in an engagement, documents and transcripts are mapped in a
harness session with the skills, as `make live` runs it, and the consultant reviews each
claim. The consultant's time is the cost, and the extractor will later reduce it.

## Decision

**The paid deliverables come before the rest of the loop.** The build order from here:

1. **Intake from an engagement folder.**
   - **The folder:** one per engagement, kept outside this repository, since it holds
     client data. A configuration names the processes and says which file belongs to
     which view:
     - documents and policies, as written;
     - transcripts, as told;
     - exports, as done.
   - **One command:**
     - ingests the documents and transcripts as sources, listing the people in them as
       subjects so that they are sealed;
     - runs `discover` and `conform` on the exports;
     - lists what is left for a person: the sources that no claim cites yet.

     Running it again over an unchanged folder writes nothing.
   - **Parsers:** PDF and Word files go through an existing parser at the edge, after ADR
     0012's licence check.
   - **Northwind** becomes a folder of this shape, so that CI runs the intake.
2. **The controls test.**
   - **The test:** each rule written in the client's policy is tested against every case
     of the export.
   - **The output:** an exceptions register. It lists each case that broke each rule, with
     its date and the values the rule read, and cites the rule's sources and the export.
   - **Its ADR decides** how rules beyond routing are represented:
     - precedence, such as an order only after an approval;
     - timing, such as approval within three days.
   - **Segregation of duties** comes later, with its own ADR. It needs the person behind
     each event, which is personal data, sealed like the interviews.
3. **Change-approval evidence.** `wmk-software forge audit` over an audit period, read as
   of the period's end and written as a document in the report's form.
4. **Re-runs.** The same folder with a new export. The report and the register say what
   changed since the last run, from as-of reads. One engagement becomes a recurring one.

Then, when a paying pilot needs them:

5. **The first workflow adapter**, for the runtime the pilot uses, or Operaton when the
   client has none.
6. **The automation half of `make demo`, and the write-up.**

**`make demo` comes with the controls test,** as Northwind run as an engagement: the
folder goes in, and the report and the controls test come out. The automation joins it
after step 5.

**What waits** until an engagement needs it:
- recall into context;
- the research eval improvements;
- bulk extraction;
- declared dependencies between packs;
- an agent that writes new packs.

**Open and private, unchanged.** Engagement folders, client data, packs refined on
engagements, playbooks and prices live in private repositories. This repository holds the
tools, Northwind and the evals.

## Consequences

**Earlier ADRs:**
- **ADR 0026** still sets the direction. Its order gives way to this one, and so does its
  premise that the demo comes before client work.
- **ADR 0033's** order is replaced from its third step. Ranking and the report stay as
  built.
- **CLAUDE.md and the README** record the new order. The use-cases page marks the products
  this order builds first.

**What CI can show:** Northwind's folder, taken from intake to the report and the controls
test with no hand edits. Whether clients pay is measured outside this repository.

**Risks:**
- **Supervised mapping:** the deliverables are only as good as it is, and the consultant's
  time is their main cost.
- **Export formats:** client exports may come in formats the adapter cannot read yet. The
  intake reads the formats already supported (CSV with a column map, XES, OCEL 2.0). A new
  format is a new reader, added when a client brings one.
- **Building ahead of the first engagement:** each step is small and stands alone, so the
  order can change once the first engagement starts.

**Still out of scope:**
- hosting a service that clients sign into: engagements run the stack on the consultant's
  machine or the client's;
- a pack registry, pack SQL, and capture of agents' own sessions.

---
name: process
description: >
  Process pack for the World Model Kernel: map how work happens in any organisation, as
  told in interviews, as written in SOPs and documents, and as done in event logs:
  processes made of steps joined by flows with conditions, what starts and ends them, the
  roles, units, systems and business objects involved, and their measures. Use with the
  core skill when mapping or comparing how a process runs, finding where practice departs
  from the documented process, or preparing a process for automation.
metadata:
  version: "0.5.0"
  kernel: ">=0.5 <1.0"
  namespace: process
  requires: world-model-core
---

# Business processes (pack)

Process structure goes in the `process` namespace. Allowed kinds there: `process`,
`activity` (a step of work), `gateway` (a decision, or a split into parallel work),
`trigger`, `outcome`, `role`, `unit` (a department or team) and `kpi`. Systems
(`component`) and business objects (`data`, such as a purchase request or an invoice) stay
in `core`.

The representation is runtime-neutral on purpose (ADR 0027). Workflow adapters compile it
into Operaton or Camunda, n8n, RuleGo or code, so write what the source says about the
work, never a particular tool's notation.

## Map it

| Fact in the source | Operations |
| --- | --- |
| A step belongs to a process | `activity part_of process` |
| Step A is followed by step B | `A flows_to B` |
| A decision ("if it is over 10,000 euros") | a `gateway`; `A flows_to` it; one `flows_to` out per branch, each with `props.when` |
| Work that splits and runs in parallel | a `gateway` whose flows out have no `when` |
| What starts a case ("a request is submitted", "every Monday") | `trigger part_of process`, `trigger flows_to` the first step |
| What a step waits for ("until the supplier confirms") | `step flows_to trigger flows_to` the next step |
| How a case ends ("order sent", "request rejected") | `outcome part_of process`, `last step flows_to outcome` |
| Who does a step | `role responsible_for activity`, or `unit responsible_for activity` |
| Who holds a role | `agent implements role`, dated with `valid_from`/`valid_to` |
| Who belongs to a team or department | `agent part_of unit` |
| A step is done in a system | `activity performed_in component` |
| A step or process works on a business object | `activity handles data`, `props.qualifier` (creates, reads, approves) when the source says |
| A measured value ("about 120 requests a quarter") | a `kpi` named for what it measures, `measures` the process or step, `props.value` and `props.unit`, the period as `valid_from`/`valid_to` |
| A policy ("requests over 10,000 euros must be approved by the controller") | promote a `normative` claim `about` the step or gateway |
| A target ("approval within two days") | a `normative` claim `about` the KPI |
| What happens in practice, against the policy | a `descriptive` claim, `contradicts` the policy |

Name steps for the work, specifically enough to be unique across processes: "Review
purchase request", not "Review". Use the same names as the people and documents you map,
so the three views meet on the same nodes.

## Conditions

A flow out of a decision carries `props.when`, written in this grammar and nothing else:

```
amount > 10000
amount > 10000 and not urgent
category in ["it", "facilities"] or amount <= 500
else
```

- Compare a case attribute with a literal: `= != < <= > >=`, or `in [...]`.
- Join comparisons with `and`, `or`, `not` and parentheses. A bare attribute means
  `= true`.
- Numbers have no units or separators: `10000`, not `€10,000`.
- Strings go in double quotes. `else` is the branch taken when no other holds.
- Attribute names are lower case with underscores, as the case's records name them when you
  know (`amount`, `cost_centre`).

Write the canonical form: attribute on the left, lower-case keywords, single spaces, and
the parts of an `and` or `or` in alphabetical order (`amount > 10000 and urgent = true`).
Props are part of an edge's identity, so two sources stating the same condition must
write it identically to meet on one edge. The source's own words go in the claim text.

A flow with `when` is a routing rule: every case at its start for which the condition
holds continues at its end. A flow without `when` says only that work passes from one step
to the next. If the source states a condition you cannot express this way, leave `when`
out and promote a claim with the condition in words, `about` the gateway.

## Three views

Each view is its own source, so belief counts each once and shows where they disagree.

- **As told:** interview turns (the interview skill). Ask "Is that the rule, or what
  actually happens?" whenever someone says "supposed to".
- **As written:** SOPs, policies and process documents, mapped in an extraction run per
  document.
- **As done:** event logs, mapped by the adapter. Never map a log by hand.

A step or flow one view asserts and another denies is **contested**: that is the
discovery. A fact only one view states is not contested, but it is unconfirmed; the
queries below find both.

## A client's folder: intake, then the mapping

`wmk-process intake engagement.yaml --work work.md` takes a client's engagement folder in
one pass (ADR 0036; `packs/process/engagements/northwind/engagement.yaml` is an example).
It stores the documents, creates each interviewee by the claim of their consent, stores
each of their turns sealed under their key, and runs `discover` on the exports. It writes
no claim about what a document or a turn says: that is yours, with the consultant
reviewing each claim before it is written.

- **Work from the work file.** It lists each source no claim cites yet, with its chunk ids
  and words. The sources are stored already: cite those chunk ids as `claim.source` and
  quote the chunks' words. Never ingest them again: a copy that differs by one space is a
  second source, and the stored one stays waiting.
- **The speaker exists.** A turn's speaker is its author, given as an agent id. Name that
  agent in claims about what they do (`implements`, `part_of`); never create them again.
- **Look up before you create.** `discover` has already written the process, its steps,
  roles and system from the log: find them with `lookup_entities` and reuse them.
- **Run intake again when you are done.** It checks conformance once nothing is waiting,
  and lists anything you missed.

## Map an event log with the adapter

`wmk-process` reads CSV, XES and OCEL 2.0 JSON logs, configured by a YAML file. The file
names the process, the columns, the case attributes, a map from the log's activity labels
to step names, the system and the case object
(`packs/process/evals/fixtures/northwind-purchase-requests/northwind.yaml` is an example):

```sh
uv run wmk-process plan northwind.yaml                                # the claims discover would write
uv run wmk-process discover northwind.yaml --url http://localhost:8000/mcp
uv run wmk-process conform northwind.yaml --url http://localhost:8000/mcp
uv run wmk-process compare northwind.yaml --url http://localhost:8000/mcp   # writes nothing
uv run wmk-process rank northwind.yaml --url http://localhost:8000/mcp      # writes nothing
uv run wmk-process report northwind.yaml --url http://localhost:8000/mcp    # writes nothing
```

- **`discover`** writes the log's view without reading the model: a digest of the log as
  the source, then observed claims citing its lines. The claims cover the steps, the
  directly-follows flows, the roles (from `org:role`), the system and the case object. Its
  KPIs: cases and median cycle time of the process; for each step, executions, repeats (a
  case running it again), the median and total hours since the case's previous event, and
  handoffs (the previous event was another role's). People (`org:resource`) are not mapped.
- **`conform`** checks the process as the kernel holds it against the same log. It writes
  one verdict per step, flow between steps, and decision branch whose condition the log
  can evaluate. A branch is denied when some cases that met its condition went elsewhere,
  with the counts and example cases in the claim. What it cannot check is listed in its
  digest.

- **`compare`** reports where the views agree and disagree. The file's `views:` names the
  collections of each view (the SOP's, each interview session's); the view as done is the
  log's and its conformance digest's. For each step and flow it gives the kernel's belief
  and each view's stance: asserts (with its window), denies, divided (its sources
  disagree) or silent. It groups them: contested, denied, stated by one view only, agreed.
  It reads the log's operations, never claim text, so it shows nothing sealed.
- **`rank`** scores each step with executions measured on six factors from 0 to 1: volume,
  waiting (total time before it), rework, handoffs, rule (a routing rule into it that some
  source denies) and system (performed in one). The score is their sum, each times its
  weight (`rank.weights` in the file, 1 by default). Each step lists the contested facts to
  settle before automating it; steps never measured and decisions already written as rules
  are listed apart. `--json` gives every input's edge id, belief and window. A score says
  where automation would pay, not whether a step can be automated: ask the people who run it.
- **`report`** writes the discovery report in Markdown: the process as mapped in flow order,
  every source's stance where the views disagree, the measures and the ranking. Each
  sentence about a fact ends with footnotes citing the claims whose assertions count for
  it, with the words of their sources. It opens sealed sources for the reader, attributes
  claims to collections and never to people, and shows an erased person's words as
  [erased]. `--json` gives each sentence with its edge and claim ids, the shape `cite`
  takes. Conclusions and the questions for the next interview are yours to write.

Run `discover` after a new export, and `conform` after the told and written views are
mapped and again after they change; `intake` does both for a folder. Map the label map before conforming: a step whose
name matches no log label is reported as never run. Run `compare`, `rank` and `report` after
`conform`.
`make northwind` does all of it for the Northwind scenario: intake over its folder, then the
`northwind-views` fixture (the tool calls you would make for its SOP and interviews), then
intake again and the report.

## Questions for the discovery

Contested steps and flows, with both sides counted:

```cypher
MATCH (s:Entity)-[:part_of]->(p:Entity {id: $process}), (s)-[f:flows_to]->(t)
WHERE f.belief_status = 'contested'
RETURN s.name, t.name, f.props, f.sources_for, f.sources_against
```

Flows only one view states (one origin, nobody against):

```cypher
MATCH (s:Entity)-[:part_of]->(p:Entity {id: $process}), (s)-[f:flows_to]->(t)
WHERE f.origins_for = 1 AND f.sources_against = 0
RETURN s.name, t.name, f.props
```

Handoffs, where work passes between roles:

```cypher
MATCH (p:Entity {id: $process})<-[:part_of]-(a:Entity)-[f:flows_to]->(b:Entity)-[:part_of]->(p),
      (ra)-[:responsible_for]->(a), (rb)-[:responsible_for]->(b)
WHERE ra.id <> rb.id
RETURN a.name, ra.name, b.name, rb.name, f.belief_status
```

Steps with no role, no system or nothing after them:

```cypher
MATCH (s:Entity)-[:part_of]->(p:Entity {id: $process})
WHERE s.kind = 'activity'
  AND (NOT EXISTS((s)<-[:responsible_for]-()) OR NOT EXISTS((s)-[:depends_on]->())
       OR NOT EXISTS((s)-[:flows_to]->()))
RETURN s.name
```

Measures, current and past:

```cypher
MATCH (k:Entity {kind: 'kpi'})-[m:monitors]->(x)
RETURN k.name, x.name, m.props, m.valid_from, m.valid_to, m.belief_status
```

## Rules this pack adds

- `process.allowed_kinds`: the kinds above.
- `process.flows_between_steps`: `flows_to` goes from an activity, gateway or trigger to an
  activity, gateway, trigger or outcome.
- `process.performed_in_component`, `process.handles_data`, `process.kpi_measures`: each edge kind
  connects only the kinds in the table above.

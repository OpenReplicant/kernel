---
name: research
description: >
  Research pack for the World Model Kernel: map papers into sourced findings, hypotheses
  and open questions, with the methods, data and measures they concern, and weigh the
  evidence across papers. Use with the core skill when reading abstracts or papers, or
  when asked what a field knows, where findings disagree, or what is still open.
metadata:
  kernel: ">=0.1 <1.0"
  namespace: research
  requires: world-model-core
---

# Research (pack)

Papers, methods, data and measures go in the `research` namespace. Every result a paper
reports is a **finding**: a Claim node of its own, linked to the paper that reports it.
Agreement and disagreement between papers are edges between findings, so the evidence on
any question can be counted and traced to papers.

## Ingest a paper

One source per paper. `content`: the title, an authors line, venue and year, the DOI or
arXiv id, then the abstract (and the full text when you have it), as Markdown.

- `title`: the paper's title. `media_type`: `text/markdown`.
- `uri`: `https://doi.org/<doi>`, else `https://arxiv.org/abs/<id>`.
- `collection`: `doi:<doi>`, else `arxiv:<id>`, so a preprint and its published version
  count as one source.
- `metadata`: `{"doi", "arxiv", "year", "venue"}` as known.
- Leave `author` unset; authors are recorded as people in the graph.

## Map it

| Fact in the paper | Operations |
| --- | --- |
| The paper itself | `create` a `paper` with `identity` `{"doi": ...}` or `{"arxiv": ...}` (no version suffix), `props` `{"year", "venue"}` |
| Its authors | `create` human Agents (`identity` `{"orcid": ...}` when given); `authored` from each person to the paper. Look names up first: the same person writes many papers |
| A method, model or intervention it proposes | `create` a `method`, `introduces` from the paper. Acronyms go in `aliases` (`Retrieval-gated decoding`, aliases `["RGD"]`) |
| A variant of an existing method | the new `method` `builds_on` the original |
| A data set, corpus, benchmark or cohort | `data`; `uses_data` from the paper |
| A metric or outcome | `measure` |
| A result | one claim, `promote`d with `about` the method, data and measure; `reports` from the paper to it. Keep the numbers and the comparison in the text |
| A hypothesis or explanation the paper offers | modality `hypothetical`, promoted, `reports` |
| An open question or future work | modality `hypothetical`, promoted, `reports` |
| A recommendation | modality `normative`, promoted, `reports` |
| A paper it cites | `cites` to the cited paper (create it from the reference if needed) |

Background the paper attributes to other work is not this paper's finding: record `cites`,
and map that finding from the other paper when you have it.

## Findings, agreement and disagreement

- Each paper's result is its own Claim node, even when it repeats another paper's result.
  Never assert one paper's result on another paper's claim.
- Relate findings with `supports` (a consistent result, a replication), `contradicts` (a
  conflicting result, or a result against a hypothesis) and `refines` (the same result,
  narrower or more precise).
- **Who says so sets the basis.** When the paper itself relates its result to another
  ("in line with", "contrary to"), assert the edge in that finding's own payload (basis
  `reported`, citing the chunk). When you relate findings across papers yourself, write a
  separate claim saying why, with basis `inferred`, no source, and `low` or `medium`
  confidence.
- Questions about a field rest on **synthesis hypotheses** ("RGD improves factual
  consistency"): your inferences, promoted as `hypothetical` with basis `inferred`, with
  findings pointing at them through `supports` and `contradicts`.
- Disagreement between papers shows as `contradicts` edges and evidence counts, not as a
  contested status: each finding is accepted as what its paper reported.

## Reading a paper's wording

| Wording | Claim |
| --- | --- |
| "we show", "we find", reported numbers | `descriptive`, confidence `high` |
| "suggests", "indicates", "appears to" | `descriptive`, confidence `low` or `medium` |
| "we hypothesize", "is likely due to", "may explain" | `hypothetical` |
| "remains unclear", "open question", "future work should" | `hypothetical` (an open question) |
| "should", "we recommend" | `normative` |

Findings rarely need `valid_from`; the paper's year goes in its `props`.

## Questions about a field

Answer from these queries, then `cite` each sentence with the finding claims it relied on
(`claims` in each sentence). `$hypothesis` and `$method` are node ids.

Evidence for and against one hypothesis, with the paper behind each finding:

```cypher
MATCH (h:Claim {id: $hypothesis})<-[r]-(f:Claim)
WHERE r.edge IN ['supports', 'contradicts', 'refines']
OPTIONAL MATCH (p:Entity {kind: 'paper'})-[:responsible_for {kind: 'reports'}]->(f)
RETURN r.edge, r.belief_status, f.id, f.name, p.name
```

The balance of evidence on every hypothesis:

```cypher
MATCH (h:Claim {kind: 'hypothetical'})
OPTIONAL MATCH (h)<-[s:supports]-(:Claim)
WITH h, count(DISTINCT s) AS supporting
OPTIONAL MATCH (h)<-[c:contradicts]-(:Claim)
RETURN h.id, h.name, supporting, count(DISTINCT c) AS contradicting
```

Open questions: hypotheses no finding bears on yet:

```cypher
MATCH (h:Claim {kind: 'hypothetical'})
WHERE NOT EXISTS((h)<-[:supports]-(:Claim)) AND NOT EXISTS((h)<-[:contradicts]-(:Claim))
RETURN h.id, h.name
```

Everything claimed about a method, with the paper that reports it:

```cypher
MATCH (f:Claim)-[:about]->(m {id: $method})
OPTIONAL MATCH (p:Entity {kind: 'paper'})-[:responsible_for {kind: 'reports'}]->(f)
RETURN f.id, f.kind, f.name, p.name
```

Findings nobody has replicated:

```cypher
MATCH (p:Entity {kind: 'paper'})-[:responsible_for {kind: 'reports'}]->(f:Claim {kind: 'descriptive'})
WHERE NOT EXISTS((f)<-[:supports]-(:Claim))
RETURN f.id, f.name, p.name
```

Findings in conflict:

```cypher
MATCH (a:Claim)-[r:contradicts]->(b:Claim)
RETURN a.id, a.name, b.id, b.name, r.belief_status
```

The methods of a field, where each was introduced and what it builds on:

```cypher
MATCH (m:Entity {kind: 'method'})
OPTIONAL MATCH (p:Entity {kind: 'paper'})-[:responsible_for {kind: 'introduces'}]->(m)
OPTIONAL MATCH (m)-[:depends_on {kind: 'builds_on'}]->(base)
RETURN m.id, m.name, p.name, base.name
```

## Rules this pack adds

- `research.allowed_kinds`: the namespace allows `paper`, `method`, `measure`, `data`,
  `concept` and `component`.
- `research.authored_paper`, `research.reports_claim`, `research.introduces_artefact`,
  `research.cites_paper`, `research.uses_data`, `research.builds_on_method`: each edge
  kind connects only the kinds in the table above. A finding credited to an author
  instead of the paper is rejected.
- `research.paper_ids`: a DOI or arXiv id identifies a paper; a second paper with the
  same id is refused as a duplicate.
- `research.orcid`: an ORCID iD identifies a person.

The ontology is applied by `sql/10_ontology.sql`; the reasoning is in ADR 0013.

# 0013. Research findings are per-paper claims

Date: 2026-10-04 · Status: accepted

## Context

Research is the first product pack. belief_v1 marks a fact contested once both sides reach
0.25, which suits a few sources (an org chart against an interview) but not literature:
nearly every result has a dissenting paper, so asserting all papers' results on one shared
edge or claim would leave most of a field contested. Papers also cite and repeat each
other, so they are not independent sources for one shared fact.

## Decision

- Each result a paper reports is its own Claim node (`promote`), `about` the methods, data
  and measures it concerns, and linked from its paper by `reports` (a specialisation of
  `responsible_for`). Its belief is what that paper reported.
- Agreement and disagreement are kernel epistemic edges between Claim nodes: `supports`,
  `contradicts`, `refines`. When a paper states the relation, the edge is asserted in that
  finding's payload (basis `reported`); when the extracting agent draws it, in a separate
  claim with basis `inferred`.
- Questions about a field are synthesis hypotheses (hypothetical claims, usually inferred)
  that findings support or contradict. The evidence balance is a query over those edges,
  not a belief status, so belief_v1 stays unchanged.
- The pack adds kinds `paper`, `method` and `measure` (with core `data`, `concept`,
  `component`), edge kinds `authored`, `reports`, `introduces`, `cites`, `uses_data`,
  `builds_on`, and identity keys DOI and arXiv id for papers and ORCID for people.
- A paper is one source; its collection is its DOI or arXiv id, so versions count once.
- Claim node names keep the claim's whole sentence (up to 500 characters): the earlier
  120-character cut could drop a trailing negation.

## Consequences

The graph keeps who found what, and disagreement is visible as structure rather than
hidden in a status. Weighing evidence (study size, design, independence) is left to the
agent reading the counts and to a later, measured belief version. Abstracts come first;
full text needs the parser container.

# 0021. belief_v2: each origin counts once

Date: 2026-10-04 · Status: accepted

## Context

belief_v1 counts each source once, and a source is a document or a collection. That
counts the wrong thing whenever several documents come from the same people:

- one person interviewed twice is two sources;
- a press release syndicated ten times is ten;
- one group's two papers are two independent confirmations.

"Each source counts once" was meant to stop repetition from looking like corroboration,
and it does not.

## Decision

**Origins.** An origin is who a statement comes from: an author, a speaker, a publisher, a
repository. It is an opaque key `scheme:value`, for example `orcid:0000-0002-1825-0097`,
`openalex:a5023888391`, `agent:<agent id>`, `domain:reuters.com`, `repo:<url>` or
`name:<full name>`. Keys are trimmed and lower-cased (`kernel.normalize_origin`).

- A source declares its origins when it is ingested (`origins`, at most 64). The papers
  server passes a paper's authors: the ORCID iD, else the OpenAlex id, else the name. The
  software adapter passes the repository, so its files do not corroborate each other.
- Without declared origins, the origin is the source's author agent (an interview's
  speaker). Failing that, it is the source itself (`source:<source key>`, a scheme
  ingest refuses). A claim with no source comes from its writer (`agent:<id>`).
- `kernel.write` resolves a claim's origins and records them in the log entry
  (`claim.origins`), as it does trust, so replay reads only the log. Entries written before
  0.4 carry none; projection gives each of their sources its own origin, as v1 did.

**belief_v2.** For each edge, Claim node and node status:

1. Supersession is unchanged: each source key counts through its latest assertion.
2. Each counted assertion spreads its weight (trust x basis x confidence, as in v1) evenly
   over its origins.
3. Each origin adds its largest share to each side. `for` and `against` are the sums,
   rounded to four places.
4. The status rule and thresholds are v1's, and score = for / (for + against).

What this gives:

- Two sources from one origin weigh as much as the stronger of them.
- Partial overlap counts partly: a paper whose two authors include one already counted
  adds half its weight.
- Adding a source never lowers either side.
- When every source is its own origin, v2 equals v1 exactly.

**Disagreement is untouched.** Origins only stop agreement from being counted twice; they
never overwrite. If one person says yes in one interview and no in the next, the fact is
contested, as in v1. Only a newer assertion from the same source supersedes.

**Readers see the counts.** Edges gain `origins_for` and `origins_against` beside
`sources_for` and `sources_against`. Claims, `claims_view` and `query_log` show each
claim's origins. Log entries record `belief_version` 2. `belief_v1` stays defined for
comparison; the triggers maintain v2.

The kernel is version 0.4.0.

## Consequences

Interviews count each person once without any change to how they are ingested. Paper
counts now reflect authorship when the papers server supplies authors. A source mapped
from text alone needs the extracting agent to pass `origins`, or each paper counts as its
own origin, as before.

Some dependence remains uncounted:

- Syndication and copying are invisible unless the ingester names a common origin. Only
  identical content is caught, by its hash.
- Name keys can merge two people who share a name. That under-counts independence rather
  than over-counting it.
- A paper that repeats another's result is still a separate source. ADR 0013 handles this
  in the graph's structure (each paper's own finding, linked by `supports`), not in belief.

The weights are still v1's placeholders. The annotated-corpus eval (`make annotated`) now
reports how often relations are right at each confidence band. Changing the weights is a
later belief version.

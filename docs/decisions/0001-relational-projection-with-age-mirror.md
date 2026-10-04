# 0001. The graph is a relational projection mirrored into Apache AGE

Date: 2026-10-04 · Status: accepted

## Context

The design puts the graph in Apache AGE and runs Cypher for reads. Rule checks, belief,
resolution and the stale-read check need indexed, set-based SQL over nodes and edges, and
the replay test needs to diff the graph exactly. AGE assigns its own graph ids from
sequences, so ids differ between a live database and a rebuilt one.

## Decision

- The projection's canonical form is relational: `kernel.nodes`, `kernel.edges`,
  `kernel.conflicts`, `kernel.node_touches`, `kernel.claim_redactions`.
- Triggers mirror every node and edge into the AGE graph `world` (vertex labels Entity,
  Agent, Claim, Event; edge labels are the kernel edges) by writing AGE's label tables
  directly, in the same transaction. No Cypher is generated on the write path.
- Mirrored elements carry kernel ids (`id`, and `from`/`to` on edges); AGE graph ids are
  internal and never exposed by the gateway.
- `query_graph` runs read-only Cypher against the mirror. The replay diff compares the
  relational projection and the mirror by kernel id.

## Consequences

Cypher users see the same graph as SQL users. The mirror depends on AGE's label-table
layout (stable since 1.0, and AGE 1.8 invalidates its cache on direct DML). As-of reads on
record time recompute belief from assertions (`kernel.state_as_of`); Cypher filters inside
the query see current state, and the gateway drops rows whose elements were unknown at the
requested offset.

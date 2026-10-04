# 0018. Sub-models: lenses over the one graph, forks for alternatives

Date: 2026-10-04 · Status: proposed (design only; nothing here is built until reviewed)

## Context

There is one world model per database: one log, one graph projected from it. People also
think in sub-models: an area the system has learned (research on summarisation, the
software stack), an area it is exploring (open hypotheses, a new domain), and alternatives
it is trying out (a different ontology, a re-extraction with a new skill, a what-if change
to a system). The explorer's Models page already computes areas from namespaces
(ADR 0016). This ADR decides how the other kinds exist without breaking the invariants:
one write path, an append-only log, deterministic projection, belief as a pure function.

Three ways were considered: tagging nodes with a model id (rejected: a node belongs to many
models, and tags would be writes nobody can source); separate databases per area
(rejected: areas overlap, and cross-area questions are the point); and the split below.

## Decision

**A lens is a named scope over the one graph, and it is itself a claim.** Learned and
explored areas are lenses; nothing is copied.

- A lens is written through `kernel.write` as a promoted Claim of modality `normative` (it
  defines a scope), with `about` edges to its seed nodes and its definition in `props.lens`:
  `{"namespaces"?, "kinds"?, "seeds"?, "hops"?, "collections"?, "agents"?, "valid_at"?,
  "as_of_offset"?, "belief": "all" | "lens_sources"}`. Revising a lens is a new lens claim
  that `supersedes` the old one; retiring it is a transition to `withdrawn`. Lenses have
  provenance and history like any claim, and replay reproduces them.
- Evaluating a lens is a read: a kernel function `kernel.lens_members(lens_id, at_offset)`
  returns its node and edge ids, deterministically for a given offset. Both clocks apply:
  `valid_at` filters edges by their windows, `as_of_offset` reads the graph as recorded
  then (`kernel.state_as_of`).
- `belief: "lens_sources"` recomputes belief over only the assertions whose sources fall in
  the lens's `collections` or `agents` ("what do the interviews say", "what does the vendor
  documentation say"). It is the same pure function over a subset, computed at read time
  and never projected, so the graph and replay are unchanged.
- Agents use lenses through the existing read tools, not new ones: `lookup_entities`,
  `get_schema_slice` and `query_log` take an optional `lens`, and `query_graph` receives the
  lens's node ids as `$lens` for `WHERE n.id IN $lens`. The seven tools stay seven.
- An area being explored is a lens plus the open hypotheses `about` its nodes; how much
  attention it gets is a work-queue question, left to the vital-signs design.

**A fork is a separate database replayed from the log, for alternatives that change
structure.** In-graph alternatives stay in the graph: a hypothetical claim with `assumes`
edges is a scenario, and a lens over the claims that assume it is that scenario's model.
A fork is for what cannot live beside the main graph: a different ontology, a
re-extraction with a new skill or model, a simulated change to a system, concept
formation's shadow copy.

- Creating one is an operator action, like installing a pack:
  `python -m kernel.fork create <name> --from <db> --at <offset>`. It creates
  `<db>__<name>`, applies the kernel SQL and the source database's pack manifests, and
  replays entries up to the offset, which reproduces the graph exactly (invariant 3; this
  is the replay check run as a feature). The fork records its origin (database, offset,
  the hash of the last entry) in a metadata table, as `kernel.packs` records installs.
- The main model knows its forks: the operator writes an Event (`occurrence`, props
  `{database, origin_offset}`) to the main log through `kernel.write`, and the Models page
  lists forks beside lenses and areas.
- A fork is a full kernel. Writes go through its own `kernel.write`, under its own
  gateway and agent, with the same rules. Its offsets continue from the origin
  independently; ids are ULIDs, so nothing collides.
- Comparing a fork with its origin uses replay's graph diff: what the alternative adds,
  retracts or contests.
- **Nothing is merged by copying rows.** A fork's outcome returns to the main model in one
  of two ways. It can come back as evidence: its report is ingested as a source, and
  claims cite it. Or it can come back as a proposal carrying payloads. Those payloads
  are re-submitted through the main `kernel.write` after approval (ADR 0019), with the
  fork's origin as `read_at_offset`. The stale-read check then refuses any payload whose
  nodes changed since the fork was taken, and the remedy is a new fork from the current
  head, like a rebase.
- Forks are disposable: dropping one is an operator action, and the main log keeps the
  Event, which transitions to `completed` or `cancelled`.

## Consequences

The index of models is: the whole graph, its areas (computed from namespaces), its lenses
(claims) and its forks (events). The explorer's graph view gains `#/graph/lens:<id>`.

Lenses add no write path and no projection; their cost is query time. Forks cost a replay
of the log up to the fork point, which grows with the log. Periodic template databases
(`CREATE DATABASE ... TEMPLATE`) can shorten that later, at the price of a snapshot to
keep exact.

A fork's pack manifests are the ones its source database recorded, so a fork cannot test
an ontology change unless the operator installs the changed pack into the fork. Doing so
is the point of such a fork, and the result comes back only as a proposal.

Open: who may create forks once workers exist (the job-queue ADR), and whether lens
membership should be cached for large graphs (a read-side cache, never projection).

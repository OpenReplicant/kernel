# D-83 spike: a live graph and detectors inside the kernel

Date: 2026-10-18. Postgres 17.11 with Apache AGE 1.7.0. The files are in `docs/spikes/D83-age/`:

- `Dockerfile`: the image.
- `graph.sql`: the sync, the detectors and the events.
- `spike.py`: the checks and the benchmark.

The spike made no change to the kernel. It loads `001_kernel.sql`, `002_bootstrap.sql` and the packs into a scratch database, then adds `graph.sql` on top.

**Result: it passes.** All 11 checks pass. At about 50k claims, the graph adds 2.4 ms to a 10-claim apply, and three detectors add a further 22 ms.

## Packages

- **No need for Postgres 18.** AGE 1.7.0 is an Apache release for Postgres 17 as well (`downloads.apache.org/age/PG17/1.7.0`), so D-83's "Postgres 18" premise was wrong. PGDG packages it as `postgresql-17-age=1.7.0~rc0-1.pgdg12+1`. The `~rc0` suffix names the tag that was voted, which is the released source. The package installs beside pgvector on our current image, so the change is one apt line and `shared_preload_libraries = 'age'`.
- **AGE 1.8.0 is newer but not usable yet.** It is released for Postgres 18 only, and the Postgres 17 build of 1.8.0 is still an rc. Moving to 1.8 and Postgres 18 can be a later, separate step.
- **Licence:** Apache-2.0.

## What was built (`graph.sql`)

**Sync.** Triggers on `kernel.claim` (insert, redaction) and `kernel.claim_status` (insert) keep a property graph `kkg` in step with current claims:

- Entities are `Entity` vertices.
- Each current claim whose object is an entity becomes a `Claim` edge carrying `{id, p, scope, evidence}`.
- Superseding, retracting or redacting a claim removes its edge. If a status changes back, the edge returns.

The triggers fire inside the apply transaction, so the graph is never behind. They write the label tables directly with indexed upserts, which is what AGE's own loaders do.

**Detectors.** A detector is a row in `kernel.detector`, and there are three kinds:

| Kind | Pattern |
| --- | --- |
| `artifact` | a media type (`text/%`) |
| `claim` | an `S P O` pattern |
| `shape` | an openCypher query taking `$anchor` and returning `key, nodes, claims` |

- Registering a shape detector compiles it into a function, so its plan is prepared once per session.
- At commit, a deferred trigger runs each detector against what the transaction touched. It runs once per changed vertex for shapes, and once per changed claim or artifact for the other two kinds.
- It diffs the result against `kernel.detector_match` and records `formed` and `dissolved` rows in `kernel.event`. Those rows hold ids only. It then sends `NOTIFY kernel_event`.

**Access.**

- The graph is read-only to agents.
- Row-level security on the edge table reuses `kernel.can_read(scope)`.
- An event is visible only to a reader who can read every claim it names.

## Checks (`python docs/spikes/D83-age/spike.py`)

All 11 pass. Every write went through the real path: an agent submits a change set and a validator applies it.

1. Typing three posts forms three `unheld-post` matches.
2. Adding `org:holds` dissolves them, and the claim detector forms.
3. A two-hop shape (who manages whom: holds, reportsTo, holds) forms across change sets.
4. A supersede (the buyer now reports to the director) dissolves `lee|dana` and forms `lee|sam`.
5. After the supersede, the old edge has left the graph and the new one is in it.
6. A steward's redaction:
   - removes the edge;
   - forms `unheld-post` again;
   - dissolves both `manager-of` matches;
   - dissolves the claim detector.
7. A change set that fails part-way leaves no edge and no event, because the trigger work rolls back with the apply's subtransaction.
8. Row-level security: an uncleared agent sees neither the secret edges in Cypher nor their events, while a cleared agent sees both.
9. Agents cannot write the graph: Cypher `CREATE` is refused.
10. An artifact detector forms on a new `text/plain` artifact.
11. Agents can query the live graph in openCypher.

## Cost

The benchmark loads 1300 organisations (50,700 claims) in change sets of 390, then applies 50 change sets of 10 claims each. Latency covers validate, apply and commit.

| | Bulk load | Apply p50 | Apply p95 |
| --- | --- | --- | --- |
| Kernel as today | 0.28 ms/claim | 11.5 ms | 15.5 ms |
| + live graph | 0.70 ms/claim | 13.9 ms | 18.6 ms |
| + 2 shape detectors + 1 claim detector | — | 35.6 ms | 51.7 ms |

The first versions were 5–10× slower. Three lessons came out of fixing that, and they belong in the design:

- **Write the label tables in SQL; don't use Cypher `MERGE`.** `MERGE` did not use the indexes, and it cost 3.3 ms per claim at 50k claims.
- **Anchor shape queries one vertex at a time, with `x.iri = $anchor`.** `IN $list`, `UNWIND`, or an `OR` across pattern nodes made AGE scan every edge, costing 50–200 ms. A pattern with several nodes anchors each node in its own `UNION` branch. A small helper could generate those branches from a single `MATCH`.
- **Compile each detector into a function.** Planning a four-branch `UNION` on every call cost 8.6 ms per anchor. With the plan cached, 20 anchors cost 4 ms.

## Rough edges to settle in the build

- **Readers need `ag_catalog` on their `search_path`.** AGE resolves its operators by search path. The MCP server and `kk.lib` would set it per connection.
- **Every vertex is visible to every reader.** Vertices carry no scope, because `kernel.entity` has none in v0. A secret post's IRI shows as a vertex with no edges. This matches the kernel today, but scoped entities would be a separate decision.
- **Each match must name its nodes.** A shape match lists every vertex whose edges it reads, including those under a negated pattern, so that a change at any of them re-checks it. Detector authors need this rule, and a registration check could enforce part of it.
- **Detection runs before commit, so slow detectors slow applies.** Detection runs in the applying transaction, just before it commits. A per-detector time budget, or moving heavy detectors to an asynchronous pass over `kernel.event`, can come later.
- **Literal-valued claims are not in the graph.** Claims such as labels are reachable by claim detectors and SQL, not by Cypher. If shapes need them, they could become vertex properties later.
- **Reactor cursors were not built.** They are a table of `(reactor, last event id)` plus `kk.lib` helpers. They need no new mechanism.

## Proposed next step

Confirm D-83 with these amendments:

- AGE 1.7.0 on Postgres 17, not Postgres 18.
- Shape detectors anchor on `$anchor`.
- Each detector is compiled when it is registered.

Then the build:

1. Fold `graph.sql` into a kernel migration, with kernel tests.
2. Add events and cursors to `kk.lib` and the MCP server.
3. Move `map-gaps` over as the first user: its gap kinds become shape detectors, and the module becomes a reactor.

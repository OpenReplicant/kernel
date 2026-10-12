# The core (S1)

The conventions of `docs/DESIGN.md` as code. Facts it relies on are in `docs/SPIKE_NOTES.md`.

## Layout

| Where | What |
|---|---|
| graph `$KERNEL_GRAPH` (default `entity`) | everything the entity knows |
| schema `kernel` | the outbox, guard limits; relational because it is operational, not knowledge |
| `db/core.sql` | installs both; idempotent |
| `kernel/` | Python: `db`, `claims`, `modules`, `bridge`, `rulego`, shared chain nodes in `kernel/nodes/` |
| `rulego/` | the RuleGo-Server build (v0.38.0 + `x/python`) and config |
| `db/agensgraph/` | the database image: AgensGraph v2.18.4.0 + pgvector v0.8.7 |

## Core labels

| Label | Kind | What |
|---|---|---|
| `thing` | vertex | Base label for whatever claims are about. Module labels inherit from it, so `MATCH (t:thing)` finds them all. A thing holds identity (`key`) and derived data (an embedding); facts about it are claims or fact edges |
| `claim` | vertex | A claim that needs its own evidence, can be disputed or superseded, or is talked about |
| `source` | vertex | Where a claim came from: a document, a command, a change, a run. Kept unchanged; identified by `sha256` of its content |
| `agent` | vertex | Who wrote it: a module (`module:<name>`), a person, a model |
| `module` | vertex | An installed module: name, version, manifest hash, chains, activations |
| `about` | edge | claim → the thing it is about |
| `object` | edge | claim → a thing as its value (else the claim has a literal `value`) |
| `cites` | edge | claim → source, with `span` |
| `made_by` | edge | claim → agent |
| `supersedes` | edge | new claim → the claim it replaces |
| `derived_from` | edge | claim → the claims it was computed or inferred from |

Only the module loader creates labels: Cypher would silently create a label it hasn't seen,
so an event trigger refuses label DDL in the graph unless the transaction set
`kernel.allow_ddl`, and a trigger on the base tables refuses unlabelled elements.

## Claims

A claim vertex has `predicate`, `value` (or an `object` edge), and:

| Property | Set by | Meaning |
|---|---|---|
| `method` | writer | `observed`, `stated`, `inferred`, `computed` or `defaulted` |
| `confidence` | writer | 0..1 |
| `source` | writer | id of the cited `source` vertex |
| `span` | writer | where in the source; **required** for `observed` and `stated` |
| `agent` | writer | id of the `agent` vertex |
| `valid_from`, `valid_to` | writer | when it is true in the world (open if absent) |
| `recorded_at` | **database** | when the entity learned it; a writer's value is overwritten |
| `retired_at`, `status` | **database** on supersede/retract | when it stopped being held; `superseded` or `retracted` |
| `causation`, `depth` | **database** | the activation that wrote it (from the transaction's settings) |

Times are ISO-8601 UTC strings of fixed width (`2026-10-12T12:43:06.560802Z`), which order
correctly as text (a `timestamptz` can't be a generated column: the cast isn't immutable).

**When a claim is an edge.** A module's ontology declares each relation `as: claim` or
`as: edge`. A fact edge carries the same provenance properties as a claim and follows the same
rules; it can't be cited, disputed or talked about.

**Enforced by the database** (`BEFORE` triggers on every claim and fact-edge table, which
Cypher writes go through):
1. Provenance on insert: `method` in the list, `confidence` in 0..1, `source` and `agent`
   present, `span` for observed/stated.
2. `recorded_at`, `causation` and `depth` are stamped by the database.
3. No edits: the only change allowed is setting `retired_at` + `status` once. Nothing is
   deleted.

**Supersede** (`kernel.claims.supersede`) writes the new claim, an edge `supersedes` → old,
and retires the old one, in one transaction. `retract` retires without a replacement.

**Trace.** `trace(claim)` follows `derived_from` recursively and returns each claim with its
method, agent, source and span: how the entity came to hold it, down to the observations.

**As of.** `as_of(subject, predicate, known_at=T, valid_at=V)` returns the claims that were
held at T (`recorded_at <= T` and not retired by T) and true at V. Default: now.

## The outbox and the loop guard

Every kernel-managed table except the structural edges (`about`, `object`, `cites`,
`made_by`, `supersedes`, `derived_from`) has an `AFTER` row trigger writing `kernel.outbox` (label, op, element id, old and new properties,
causation, depth, txid) and `NOTIFY outbox`.

A writer runs inside an activation: the shared write node (or `kernel.db.activation`) sets
`kernel.causation` = `outbox:<seq>` of the row that started it and `kernel.depth` = that row's
depth + 1. A direct write (a person, a test) has depth 0.

**The guard** is in the outbox trigger, so no writer can skip it. Limits come from
`kernel.config`, not from settings a writer could change:
- `max_depth` (default 8): a write deeper than this aborts its transaction;
- `max_fanout` (default 1000): one transaction writing more rows than this aborts.
A missing limit is an error, never "no limit".

## The bridge

`kernel/bridge.py` (`python -m kernel.bridge`) delivers outbox rows to the chains that
modules subscribe (activations). It LISTENs and catches up on start. For each pending row
(oldest first, `SKIP LOCKED`) it calls each subscribed chain's synchronous execute API with
`causation` and `depth` as metadata, recording successes per chain so a retry skips them.
Outcomes on the row: `done`; `stopped` when a chain failed on the loop guard (not retried);
`dead` after `max_attempts` failures (default 5), each followed by a backoff (1, 2, 4 … 60 s).
RuleGo being unreachable is not a failure: rows wait. Rows with no subscriber are `done`.
Delivery is at-least-once: steps must be idempotent.

## Modules

A module is a directory with `module.yaml`:

```yaml
module: notes
version: "0.1.0"
types: [ontology, reasoner]      # what it contributes: perceiver, reasoner, recaller, effector, ontology
depends: []                      # other modules, installed first
vars: {band: 0.5}                # added to every chain's vars (steps read them)
ontology:
  things:                        # vertex labels, inherit from thing
    - {label: note, key: name, vectors: {embedding: 3}}   # vector(3) column + HNSW index
  relations:
    - {name: notes.status, as: claim}                     # a predicate of claim vertices
    - {name: links_to, as: edge, from: note, to: note}    # an edge label with provenance
models:                         # model roles (needs depends: [models]; docs/MODELS.md)
  summarizer: {model: claude-code-haiku, budget: {tokens: 100000}}
chains: [chains/set_status.json]   # RuleGo chain files
activations:                     # what wakes a chain
  - {chain: notes_echo, when: change, label: claim, ops: [INSERT], where: {predicate: notes.ping}}
```

`kernel.modules.install(path)`: installs dependencies, creates the labels (with indexes,
provenance triggers, outbox triggers), records an `agent` and a `module` vertex and the
module's model roles (as `defaulted` claims), and deploys the chains to RuleGo with
`${module_dir}` and `${kernel_dir}` substituted and the chain vars `module` and `version` set. Reinstalling the same version with a different manifest is an
error; a new version retires the old module vertex and its agent. Chain ids must start with
`<module>_`. `when: change` is the only activation the bridge handles; a schedule is a chain's
own endpoint, and on command is the chain's REST API.

## Shared chain nodes

`x/python` scripts under `kernel/nodes/`; their request comes in the message, so any chain can
use them after a step that shapes it:

| Node | Message in | Out |
|---|---|---|
| `graph_read.py` | `{cypher, params}` | `{rows}`; params bind as jsonb (`%(name)s`); runs in a read-only transaction |
| `graph_write.py` | `{source, things, claims, edges}` | `{written: {source, things, claims, edges}}` with ids; the agent is the chain's module and version |
| `vector_search.py` | `{label, property, vector, k}` | `{rows: [{id, key, distance}]}` |
| `model_call.py` | `{model_call: {role, prompt \| messages, system, inputs}}` | `{model_reply: {text, model, usage, cost_usd, source}}`; resolved, budgeted and recorded in the graph (`docs/MODELS.md`) |

`graph_write` passes the message's other fields through, and writes nothing (not even a source)
for a message with nothing to write, so it can sit in the middle of a chain. A claim may carry
`derived_from: [claim ids]`, and `if_current: [claim ids]`: a compare-and-set, written only if
the held claims with that subject and predicate are exactly those (else listed in
`written.skipped`). Writes that replace or compare are serialized per subject and predicate
(a transaction-scoped advisory lock).

Claims in `graph_write` may say `replaces: current`: supersede the held claims with the same
subject and predicate. Without a `source`, a write caused by a change cites that change
(`source {kind: change, uri: outbox:<seq>}`); a write with neither is refused.

## Running

```sh
KERNEL_GRAPH=test_kernel tools/dev.sh up      # database container + RuleGo on the host
export DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres KERNEL_GRAPH=test_kernel
pytest                                        # drops and recreates the test graph
python -m kernel.bridge                       # the bridge, for anything beyond the tests
```

The RuleGo server's `exec` node is disabled (`cmd_white_list` names no real command): steps
run on `x/python` only.

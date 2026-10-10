# The kernel

The kernel is a general world-modeling store: things, the roles they play, how they are
coupled, and assertions about all of it, each with provenance and time. It knows nothing
about papers or agents. Applications teach it a domain by registering **vocabularies**,
**constraints** and **views**, and talk to it only through the interfaces below.

The paper compiler is the first application. Nothing in `kernel/` may import from `apps/`.

## Four founding rules

1. **Assertions are the atom.** Every fact is a row with provenance, confidence, a context,
   and two times: when it was true in the world (valid time) and when the kernel learned it
   (recorded time). Facts are never updated in place; they are superseded or retracted.
2. **Types are data.** Types, roles and predicates are nodes loaded from vocabulary files.
   Adding a domain adds rows, never schema.
3. **Roles are not types.** Postgres *is* a database; it *plays* the vector-store role in one
   deployment. A slot is a role; a component that fills it is a thing bound to that role in a
   context.
4. **Contexts carry scope and closure.** Every assertion lives in a context: the open world,
   a source's perspective ("according to the Reflexion paper"), a system spec, a run, or a
   staging area. Open contexts treat missing facts as unknown; closed contexts treat them as
   errors and can be validated.

## Constructs

| Construct | Stored as | Notes |
|---|---|---|
| Source | `kb.source` | Immutable file, content-addressed under `$PC_DATA` |
| Span | `kb.span` | Addressable piece of a source: page + char range, path + line range, JSON path |
| Node | `kb.node` | Anything with identity. `kind` is one of `thing`, `type`, `role`, `port`, `capability`, `predicate`, `context`, `agent`, `constraint` |
| Assertion | `kb.assertion` | Subject, predicate, object node **or** literal value, context, method, confidence, times, status |
| Argument | `kb.assertion_arg` | Extra named participants for n-ary facts |
| Evidence | `kb.evidence` | Links an assertion to the spans that support it |
| Link | `kb.assertion_link` | `supersedes`, `contradicts`, `corroborates`, `derived_from` between assertions |
| Vocabulary | `kb.vocabulary` | A loaded vocabulary file, by name, version and hash |

**Bindings, couplings and ports are assertions** using kernel predicates:
`k:plays` (thing → role, in a context), `k:has_port` (role → port),
`k:couples` (port → port, with a `kind` argument: call, event, stream, shared_state, flow,
causal), `k:is_a`, `k:part_of`, `k:refines`, `k:requires_capability`, `k:has_capability`.

## Provenance: the `method` field

Every assertion records how it was obtained. This generalizes the spec's provenance field:

| Method | Meaning | Evidence required |
|---|---|---|
| `stated` | The source says it explicitly | At least one span in the source |
| `repo` | Found only in released code | A span in the pinned repo |
| `inferred` | An agent's reading of ambiguous material | Optional supporting spans; `asserted_by` names the agent and version |
| `defaulted` | Platform default where the source is silent | None; `asserted_by` names the default policy |
| `observed` | Measured in a run | The run id; supporting artifacts as spans |
| `computed` | Derived from other assertions | `derived_from` links to its inputs |

The kernel refuses to accept a `stated` or `repo` assertion without evidence spans. A *staged* one may wait for its evidence (for example, a spec imported before its PDF is stored), but it can't be promoted until the spans exist. The database enforces this on insert and on promotion.

## Interfaces

Python package `kernel`, with every operation also available as a tool-shaped script
(`scripts/kernel_*.py`, JSON in and out) for RuleGo chains and, later, MCP.

**Sources and spans**
- `put_source(file, uri, media_type, license=None) -> sha256` (idempotent; stores by hash)
- `add_span(sha256, locator, excerpt=None) -> span_id`

**Nodes and contexts**
- `ensure_node(kind, iri, label=None, props=None) -> node_id` (idempotent by IRI)
- `create_context(kind, label, closure='open'|'closed', parent=None, conditions=None) -> context_id`
  (`conditions` records what the context's facts depend on, e.g. model, benchmark version, budget)

**Assertions**
- `assert_(subject, predicate, object=None, value=None, *, context, method, confidence,
  evidence=(), args=None, valid_from=None, valid_to=None, asserted_by) -> assertion_id`
  Checks that the predicate exists in a loaded vocabulary and that subject and object match
  its declared domain and range.
- `retract(assertion_id, reason, by)`
- `supersede(old_id, new assertion…) -> new_id`
- `link(from_id, to_id, kind)` for contradicts / corroborates / derived_from

**Queries**
- `query(subject=None, predicate=None, object=None, context=None, include_subcontexts=True,
  status=('accepted',), valid_at=None, known_at=None) -> rows`
  `known_at` answers "what did the kernel believe at time T"; `valid_at` answers "what was true at T".
- `why(assertion_id) -> provenance tree` (method, agent, spans with source and locator, links)
- `bindings(context) -> role → thing` and `couplings(context) -> port graph`

**Vocabularies, constraints, validation**
- `load_vocabulary(path)`: registers types, roles, ports, predicates (with domain, range,
  cardinality) and declarative constraints from a YAML file
- `register_constraint(vocab, name, fn)`: a Python check for rules too specific for YAML
  (e.g. the MCP rule); applications register these, the kernel runs them
- `validate(context) -> errors`: runs declarative and registered constraints. Required for
  closed contexts before they can be promoted.

**Promotion**
- `promote(staging_context, target_context, by)`: moves accepted assertions out of staging
  only if `validate` passes; records who promoted them.

**Views**
- `register_view(name, export_fn, import_fn)`: an application-defined document format backed
  by a context. `export(name, context) -> document`, `import_(name, document, context_spec) -> context_id`.
  The paper compiler registers the `agent-spec` view, whose documents are `spec.yaml` files.

## Vocabulary files

Vocabularies live in `vocab/*.yaml` in git and are loaded with `load_vocabulary`. Shape:

```yaml
vocabulary: agent-design
version: 0.1
imports: [kernel]
types:
  - { iri: ad:Paper }
  - { iri: ad:Component }
roles:                       # slot types
  - iri: ad:Evaluator
    cardinality: multi       # exclusive | pipeline | multi
    ports: [{ name: run, dir: in }, { name: pass, dir: out }, { name: fail, dir: out }]
scopes: [call, step, episode, task, lifetime]
predicates:
  - { iri: ad:scope, domain: k:Role, range: literal, max: 1 }
  - { iri: ad:implementation, domain: k:Role, range: ad:Component, max: 1 }
  - { iri: ad:param, domain: k:Role, range: literal, args: [name] }
constraints:
  - { kind: max_bindings, role_cardinality: exclusive, max: 1 }
  - { kind: required, predicate: ad:scope, for: k:Role }
```

v1 ships two: `vocab/agent_design.yaml` (slots, scopes, ports, mechanism tests) and
`vocab/infra.yaml` (asset kinds, environments, models, benchmarks).

## How the paper compiler uses it

| Compiler concept | Kernel representation |
|---|---|
| Paper PDF, repo files | Sources; quoted passages are spans |
| A paper's claims about its own design | Assertions in a perspective context "per <paper>" |
| A compiled spec | A closed `system` context: roles, bindings, couplings, params |
| `spec.yaml` | The `agent-spec` view of that context (export/import) |
| Provenance `stated`/`repo`/`inferred`/`defaulted` | The assertion's `method`, with spans as evidence |
| Spec validation | `validate(context)` with the agent-design vocabulary's constraints |
| Registry component | A thing with `k:has_capability` assertions; slot resolution is a query matching role requirements |
| Human review of a compiled spec | Assertions written to a staging context, inspected with `query(method='inferred')`, then `promote` |
| A benchmark result | An `observed` assertion in a run context whose `conditions` record model, benchmark version, budget and toggles |

## What stays outside the kernel

High-volume operational data is not assertions: `run.step` checkpoints and `run.trace`
events stay in their own tables. Mechanism tests read traces; their **outcomes** and benchmark
results become `observed` assertions, which is what later agents use as evidence.

## Deferred

A graph projection (Apache AGE) for deep traversals and vector search (pgvector) are derived
views over `kb.assertion`, so adding them later rebuilds from the log with no migration.
Entity resolution across sources (`same_as` as hedged assertions) arrives with ingestion
beyond papers.

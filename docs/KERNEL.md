# The kernel

The kernel builds and maintains an **evidence-based world model**: what is believed about
things, how they are structured and connected, and, for every belief, where it came from and
how far to trust it. It is meant for many uses. Papers are one kind of evidence; so are
direct observation (instruments, runs, an agent's own perception), reports from people,
transcripts of chats and sessions, and bulk-ingested media.

It knows nothing about any particular domain. Applications teach it a domain by registering
**vocabularies**, **constraints** and **views**, and talk to it only through the interfaces
below. The paper compiler is the first application. Nothing in `kernel/` may import from `apps/`.

## Founding rules

1. **Assertions are the atom.** Every fact is a row with provenance, confidence, a context,
   and two times: when it was true in the world (valid time) and when the kernel learned it
   (recorded time). Facts are never edited; they are superseded or retracted.
2. **Evidence is recorded, not paraphrased.** Every source of evidence (a PDF, a code
   snapshot, a chat transcript, a sensor log, a run trace) is an immutable file. A fact that
   is `stated` or `observed` points at the spans of the recording that support it.
3. **Types are data.** Types, roles and predicates are nodes loaded from vocabulary files.
   Adding a domain adds rows, never schema.
4. **Roles are not types.** Postgres *is* a database; it *plays* the vector-store role in one
   deployment. A thing is bound to a role in a context.
5. **Contexts carry scope and closure.** Every assertion lives in a context: the open world,
   someone's perspective ("according to the Reflexion paper"), a described system, or an
   observation session. Open contexts treat missing facts as unknown; closed contexts treat
   them as errors and can be validated.

## Upper ontology

`vocab/kernel.yaml` is the only vocabulary the kernel knows. It has two small parts.

**Knowing: who or what a fact comes from, and how far to trust it.** Most of this lives in
the assertion's own columns (`method`, `confidence`, `asserted_by`, times, evidence); the
predicates cover what is said *about* sources and agents.

| Construct | Meaning |
|---|---|
| `k:Agent` | Anything that asserts, observes or acts: a person, a model, a program, an instrument |
| `k:Source` | A recording: document, code, transcript, sensor log, run trace. Every `kb.source` row is also a node |
| `k:produced_by` (Source → Agent, arg `as`) | Who made the recording, and as what: author, speaker, recorder, instrument |
| `k:produced_at` | When the recording was made (not when the kernel stored it) |
| `k:holds` (Thing → Context) | The perspective context holding the claims of an agent or source |
| `k:reliability` | How far claims from a source or agent hold up, 0..1. An assertion like any other, so it carries its own evidence (e.g. reproductions) and differs per context |

Two different "who"s: `asserted_by` is the agent that **wrote the row** (the compiler, a
person, an ingestion script); the claim's **originator** is the source's producer, reached
through the evidence spans, or the holder of the perspective context it sits in.

**Structure: what things are, what they are made of, how they connect.**

| Predicate | Meaning |
|---|---|
| `k:is_a`, `k:subtype_of` | Classification; types are nodes |
| `k:part_of` | Composition |
| `k:depends_on` (arg `kind`) | Needs the object to exist or work |
| `k:plays` | Binding: a thing fills a role, in the assertion's context |
| `k:has_port`, `k:couples` (arg `kind`) | Interfaces and connections: call, event, stream, shared_state, flow, causal |
| `k:has_capability`, `k:requires_capability` | What a thing can do; what a role needs |
| `k:label`, `k:description`, `k:same_as` | Naming and (always hedged) identity |

### Methods: how a fact was obtained

| Method | Meaning | Evidence |
|---|---|---|
| `stated` | A source says it: a document, a speaker, a code file | **Required**: spans in the source |
| `observed` | Perceived or measured directly: an instrument, a run, a session | **Required**: spans in the recording (e.g. the run's trace file) |
| `inferred` | An agent's reading of or reasoning over other material | Optional spans; `asserted_by` names the agent and version |
| `computed` | Derived mechanically from other assertions | `derived_from` links to its inputs |
| `defaulted` | Assumed by a named policy where nothing says otherwise | None; `asserted_by` names the policy |

Applications may keep finer labels (the paper compiler distinguishes "the paper says" from
"only the released code shows"); they map onto these methods and keep the original label as
an assertion argument, since the source the span points into already tells them apart.

### Context kinds

| Kind | Closure | Use |
|---|---|---|
| `world` | open | The kernel's best current account |
| `perspective` | open | What one source or agent claims |
| `system` | closed | A described or designed system; validated before acceptance |
| `session` | open | An observation session: a run, a chat, a recording, an embodied episode. `conditions` record what its observations depend on |
| `vocabulary` | closed | Facts a vocabulary file loads |

Contexts nest through `props.parent`; queries include sub-contexts by default.

## Storage

| Construct | Stored as | Notes |
|---|---|---|
| Node | `kb.node` | `kind` is one of `thing`, `type`, `role`, `port`, `capability`, `predicate`, `context`, `agent`, `source`, `constraint` |
| Source | `kb.source` | Immutable file, content-addressed under `$PC_DATA`; also a node |
| Span | `kb.span` | Addressable piece of a source: page + char range, path + line range, JSON path, time range |
| Assertion | `kb.assertion` | Subject, predicate, object node **or** literal value, context, method, confidence, times, status |
| Argument | `kb.assertion_arg` | Named extra participants (n-ary facts). A predicate's declared `args` are required; others may be added freely |
| Evidence | `kb.evidence` | Links an assertion to the spans that support it |
| Link | `kb.assertion_link` | `supersedes`, `contradicts`, `corroborates`, `derived_from` |
| Status history | `kb.status_change` | Every status an assertion has had, when, and by whom |
| Vocabulary | `kb.vocabulary` | A loaded vocabulary file, by name, version and hash |

### Assertion lifecycle

```
staged ──▶ accepted ──▶ disputed ──▶ accepted
   │          │  │          │
   ▼          ▼  ▼          ▼
retracted  superseded / retracted   (final)
```

- **Staged** assertions are written straight into their target context and wait for review
  or evidence. `promote(context)` validates the context including its staged assertions,
  then accepts them. Nothing is copied or moved.
- **Supersede** writes the replacement, links it `supersedes` → old, and marks the old one
  `superseded`, in one transaction.
- `known_at=T` answers from `recorded_at` and `kb.status_change`: what was accepted at T.

### Rules the database enforces

So that no client, script or bug can quietly break the evidence trail:

1. Only `status` changes on an assertion, and only along the lifecycle above. Nothing in
   `kb.*` is deleted; sources, spans, arguments, evidence, links and status history are
   append-only.
2. `stated` and `observed` assertions cannot be accepted (or disputed) without evidence.
3. An assertion's predicate is a `predicate` node, its context a `context` node, its
   `asserted_by` an `agent` node; it has exactly one of object or value.
4. Every status change is logged with the acting agent (`set local kb.actor = '<uuid>'`).

The library checks everything vocabulary-dependent: predicate exists, domain and range,
`max`, required args, enums, and constraints.

**Domain and range.** Each kernel type is backed by a node kind (`k:Agent` → `agent`,
`k:Role` → `role`, `k:Thing` → `thing`, `agent` or `source`, …). A node satisfies a type if
its kind backs it, or if it has an accepted `k:is_a` to that type or one of its subtypes.

**Ports belong to role instances.** A role type in a vocabulary lists its minimum ports.
Each role *instance* in a system context (one per slot, say) gets its own port nodes,
`<role iri>#<port name>`, with `k:has_port`; an instance may add ports beyond the minimum.
`k:couples` connects instance ports.

## Interfaces

Python package `kernel` (`Kernel.connect()`), with the main operations also available as
tool-shaped scripts (`scripts/kernel_*.py`, JSON in and out, schemas in `scripts/schemas/`)
for RuleGo chains and, later, MCP.

Node references are a UUID, an IRI, or a CURIE whose prefix a loaded vocabulary declares
(`k:plays`). In `args`, a `uuid.UUID` is a node and anything else a literal; in the scripts'
JSON, `{"node": ref}` or `{"value": …}`.

**Sources and spans**
- `put_source(file, media_type, uri=None, license=None) -> sha256` (idempotent; stores by hash). Who produced it is an ordinary `k:produced_by` assertion about the source's node.
- `add_span(sha256, locator, excerpt=None) -> span_id` (idempotent per source and locator)

**Nodes and contexts**
- `ensure_node(kind, iri, label=None, props=None) -> node_id` (idempotent by IRI)
- `create_context(kind, label, closure='open'|'closed', parent=None, conditions=None) -> context_id`

**Assertions**
- `assert_(subject, predicate, object=None, value=None, *, context, method, confidence,
  evidence=(), args=None, valid_from=None, valid_to=None, status='accepted', asserted_by) -> assertion_id`
- `add_evidence(assertion_id, spans, by)` (e.g. once a staged fact's source is stored)
- `retract(assertion_id, reason, by)`
- `supersede(old_id, new assertion…) -> new_id`
- `link(from_id, to_id, kind)` for contradicts / corroborates / derived_from

**Queries**
- `query(subject=None, predicate=None, object=None, context=None, include_subcontexts=True,
  status=('accepted',), method=None, valid_at=None, known_at=None) -> rows`
- `why(assertion_id) -> provenance tree` (method, agent, spans with source, locator and producer, links)
- `bindings(context) -> role → thing` and `couplings(context) -> port graph`

**Vocabularies, constraints, validation**
- `load_vocabulary(path)`: registers types, roles, ports, predicates (domain, range, `max`,
  `args`, enums) and declarative constraints from a YAML file. From then on `assert_` refuses
  (`SchemaError`) an undeclared predicate, a subject or object outside the domain or range, a
  literal outside its enum, or missing required args
- `satisfies(node, type)`: the domain/range rule below
- `register_constraint(vocab, name, fn)`: a Python check for rules too specific for YAML;
  applications register these, the kernel runs them
- `validate(context) -> errors`: each predicate's `max`, `required` constraints and registered
  Python constraints, over accepted and staged assertions in the context and its sub-contexts.
  A declared Python constraint that nothing registered is itself an error

**Promotion**
- `promote(context, by)`: accepts the context's staged assertions if `validate` passes
  (otherwise `ValidationFailed` with the errors)

**Modules** (docs/ARCHITECTURE.md)
- `load_module(name)`: finds `<name>/module.yaml` under `$PC_MODULES` (default `./modules`),
  loads its `depends` first, then its `vocab` files, then calls `register(kernel)` on its
  `python` package. Constraints and views are registered per process.

**Views**
- `register_view(name, export_fn, import_fn)`: an application-defined document format backed
  by a context. `export(name, context) -> document`, `import_(name, document, ...) -> context_id`.

## Vocabulary files

Vocabularies live in `vocab/*.yaml`, are loaded with `load_vocabulary`, and are stored in
`kb.vocabulary` by hash. Shape:

```yaml
vocabulary: agent-design
version: "0.3"                 # reloading a version with different content is an error
imports: [systems]             # vocabularies this one builds on (loaded first)
prefix: { ad: "urn:pc:agent-design:" }
types:
  - { iri: ad:Prompt, is_a: k:Thing }
scopes: [call, step, episode, task, lifetime]     # a named list, for enum_from
roles:
  - { iri: ad:Evaluator, key: evaluator, cardinality: multi, internal: true,
      ports: [run:in, pass:out, fail:out, score:out] }
predicates:
  - { iri: ad:scope, domain: k:Role, range: literal, max: 1, enum_from: scopes }
  - { iri: ad:uses_prompt, domain: k:Role, range: ad:Prompt, args: [position] }
constraints:
  - { kind: required, predicate: sys:instance_of, for: k:Role, in: system }
  - { kind: python, name: exclusive_slots }      # registered by the module's register()
```

Domain vocabularies may add their own lists (`scopes`, `asset_kinds`) and fields on roles;
the kernel stores them and makes them available to registered constraints and views.
`validate` applies the constraints of the vocabularies this kernel instance has loaded, and
reports a context that uses a vocabulary it hasn't loaded.

## What stays outside the kernel

High-volume operational data is not assertions: `run.step` checkpoints and `run.trace`
events stay in their own tables. When a run finishes, its trace is exported as a source file
(`runs/<id>/trace.jsonl`); mechanism-test outcomes and benchmark results become `observed`
assertions in the run's session context, with spans into that file.

## Deferred

A graph projection (Apache AGE) for deep traversals and vector search (pgvector) are derived
views over `kb.assertion`, so adding them later rebuilds from the log with no migration.
Entity resolution across sources (`same_as` as hedged assertions) and source reliability
scoring arrive with ingestion beyond papers.

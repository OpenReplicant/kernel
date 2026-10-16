---
name: knowledge-kernel
description: Use when reading from or writing to the Knowledge Kernel through its MCP tools — looking up what is known about an entity, explaining where a claim came from, or recording new claims, beliefs, verifications or status changes as a change set.
---

# Working with the Knowledge Kernel

The kernel stores **claims**: attributed, referenced statements about entities, each with a belief score.
You can read anything your role is cleared for. You never write directly: you submit a **change set**,
and the kernel validates and applies it (or sends it to a person for review).

## Reading

- `search(text)` → candidate entities and claims.
- `get_entity(iri)` → the entity card: current claims with status and belief.
- `query_claims(filters)` → claims by subject, predicate, object, scope, status (PostgREST filters).
- `explain(claim_id)` → sources and quotes, what it was derived from, what uses it, status history,
  beliefs and verifications. Use this before relying on a claim you didn't make.
- `list_slots()`, `list_modules()` → the platform's own self-model.

When you answer a question from the kernel, **cite claim ids and show their belief** (invariant 6).
Deprecated claims are history, not current fact; say so if you mention them.

## Writing

1. `start_run(module, plan_step?)` → run id. Every change set names the run that produced it.
2. Build the change set (schema: `kernel/schemas/change_set.schema.json`):
   - `idempotency_key`: stable, e.g. `<your module>:<source sha256>:<n>`; resubmitting is safe.
   - `attributed_to`: your own agent IRI. Attribution to anyone else is rejected.
   - `target_scope`: a scope you have write clearance for.
   - `ops`: `add_claim`, `set_status`, `add_belief`, `add_verification`, `add_entity`, `add_artifact`.
3. `submit_change_set(cs)` → `{id, status, lane}`.
4. `finish_run(run_id, status, cost)`.

Rules the kernel enforces (your change set is marked invalid otherwise):
- Every claim has a reference (`source_uri` or `artifact`) or `derived_from` claims.
- `evidence_type: extracted` claims include the exact supporting `quote`.
- `evidence_type: inferred` claims list their premises in `derived_from`, and a derived claim cannot be
  placed in a scope readable by more people than its inputs.
- Predicates must be approved properties. To propose a new one, submit `rdf:type owl:ObjectProperty`
  (or similar) for it — that goes to the review lane automatically.
- You cannot verify claims made by your own module or agent.
- Nothing is deleted: use `set_status` with `deprecated` + `reason` (`superseded` with `replaced_by`,
  `refuted`, or `retracted`). Use `expect_status` so you don't overwrite someone else's change.
- Conditions go in `qualifiers` (keys are property IRIs), never inside the value.

Belief values come from belief-model modules, not from you. Don't submit `add_belief` unless you are one.

# Knowledge Kernel

A general-purpose engine that learns from claims and acts on them. One Postgres holds every claim
(attributed, referenced, belief-scored, scoped); modules plug into slots as encoders, processors,
decoders, actuators, interfaces, agents and controls; the system describes itself in its own model.
Almost every component is existing open tooling — what this repo builds is the kernel, the slot
contracts and the default modules.

## Read first, in this order

1. `docs/spec.md` — the specification (source of truth). Kernel v0 (§3) is **frozen**.
2. `docs/milestone-1.md` — current work: tasks T0–T13 and gates G1–G7.
3. `docs/decisions.md` — decided, adopted (confirm with the owner) and open questions.
4. `kernel/schema/001_kernel.sql` and its tests — what the kernel actually enforces.

If this file and the spec disagree, the spec wins; flag the conflict instead of picking silently.

## Invariants (never weaken; changing them needs the owner's explicit decision)

1. Every claim is attributed (agent + run) and referenced; extracted claims carry the supporting quote.
2. Nothing is deleted: supersede, refute or retract by status. The only content removal is redaction,
   which keeps identity and lineage and is itself a recorded run.
3. A claim's scope is at least as restrictive as everything it was made from.
4. Clearance is granted by people and enforced by Postgres, never by a model's judgment.
5. No method verifies its own output.
6. Every answer cites claim ids and shows their belief.

## Rules for working in this repo

- **Kernel changes are off-limits** without the owner's decision. If a feature seems to need one, build
  it as a module that reads `api` views and submits change sets, or write up the case in
  `docs/decisions.md` and ask.
- **Modules never write tables.** They read through PostgREST (`api` schema) with their own JWT and
  write only by `submit_change_set`. Actuators never write claims.
- **One concept, one home** (`ontology/packs.yaml`). Before adding a term, check whether a core
  vocabulary owns the concept. New terms go through the review lane (vocabulary typing claims are
  forced there) or a pack migration.
- **Look things up; don't rely on memory** for external tools (PostgREST, RuleGo-Server, MCP SDK v2,
  SpiffWorkflow, pySHACL, image tags). Pin exact versions and record what you pinned.
- **Every module** has a `module.yaml` valid against `kernel/schemas/module.schema.json`, fills slots
  from `ontology/slots.yaml`, runs as its own `kkm_*` role, and ships its chains in the chain profile
  (`kernel/schemas/chain.schema.json`) rather than runner-specific files.
- Conditions go in qualifiers, never inside values. Ambiguous entity matches stay separate with
  `kk:possiblySameAs` for review. Telemetry is aggregated into observed claims per time window.
- Licenses: permissive or LGPL-library only in the default distribution (D-11).

## Tests

```sh
PGHOST=<socket or host> PGUSER=<superuser> sh kernel/schema/tests/run.sh   # 30 kernel checks
python shapes/tests/test_shapes.py                                         # SHACL fixtures
python scripts/check_manifests.py                                          # manifests, chains, slots
```

All three pass on the handoff as delivered (Postgres 16; rdflib + pyshacl).

## Layout

```
kernel/
  schema/        001_kernel.sql, 002_bootstrap.sql, tests/      ← frozen v0
  schemas/       change_set, module, chain JSON Schemas         ← ABI
  contracts.py   Python Protocols for module kinds              ← ABI
ontology/        kk-core.ttl, kk-tosca.ttl, kk-profile.ttl, packs.yaml, slots.yaml
shapes/          kernel.shacl.ttl + fixtures/tests
modules/         one folder per module (module.yaml, chains/, SKILL.md, migration.sql); _template/
deploy/          compose.yaml, postgres/, Caddyfile, rulego/, mcp/agents.mcp.json, .env.example
scripts/         mint_jwt.py, check_manifests.py
eval/            eval sets (data) and their format
docs/            spec, milestone-1, decisions, mcp-servers, sources
```

## Current milestone

Milestone 1 — the system models itself and hands its entrypoint to RuleGo-Server
(`docs/milestone-1.md`). Start with T0 and T1. Work one task at a time; each ends with its check passing.

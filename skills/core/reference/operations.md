# Operations

Every write is `{"claim": {...}, "read_at_offset": n, "ops": [...]}`. Ops run in order;
`$name` refs defined by a `create` (or `promote`, or an `assert` with `ref`) can be used
by later ops in the same payload. Ids from the graph never start with `$`.

## create

```json
{"op": "create", "ref": "$x", "type": "Entity", "kind": "activity", "namespace": "bpm",
 "name": "Approve invoice", "aliases": ["invoice sign-off"], "props": {},
 "distinct_from": ["ent_..."]}
```

- `type`: `Entity` (default), `Agent` or `Event`. Claim nodes come from `promote`.
- `kind`: from `get_schema_slice`. Entities: component, concept, data, source, symbol,
  role, process, activity, gateway, or a pack kind. Agents: human, machine. Events:
  occurrence, meeting, change, incident.
- `namespace`: where a pack's rules apply (`bpm`); default `core`.
- `identity`: identity keys, e.g. `{"email": "sam@acme.test"}` for a person. A match on an
  identity key is certainly the same node: use its id.
- `status`: lifecycle status (Entity active/retired, Agent active/inactive, Event
  planned/ongoing/completed/cancelled). Events also take `start` and `end`.
- `distinct_from`: ids of `high` or `ambiguous` candidates you judged to be different.

## assert

A new fact, or another source for an existing one:

```json
{"op": "assert", "ref": "$e", "edge": "implements", "from": "agt_...", "to": "ent_...",
 "valid_from": "2026-03-01", "valid_to": null, "props": {}, "polarity": "positive"}
```

`edge` is a kernel edge or a pack specialisation (`reads_from` is stored as
`depends_on` with kind `reads_from`). The same fact with an overlapping window is the same
edge; a non-overlapping window is a new edge. `participates_in` takes `props.role`.

On an existing edge (support, denial or a window change by this claim's source):

```json
{"op": "assert", "edge_id": "edg_...", "valid_to": "2026-03-01"}
{"op": "assert", "edge_id": "edg_...", "polarity": "negative"}
```

Window fields you leave out keep what this source said before, else the edge's window.

On a Claim node: `{"op": "assert", "claim_id": "clm_...", "polarity": "negative"}`.

## Kernel edges

| Group | Edges |
| --- | --- |
| Structure | `part_of`, `instance_of`, `subtype_of`, `depends_on`, `implements` (entity or agent to role), `flows_to` |
| Identity | `same_as` (via link/unlink), `denotes` (symbol to what it names) |
| Epistemic | `about`, `supports`, `contradicts`, `supersedes`, `refines`, `assumes` (claim to claim) |
| Time and causation | `participates_in` (to an event, with `props.role`), `precedes`, `causes` |
| Governance | `responsible_for`, `monitors`, `approved_by`, `rejected_by`, `verified_by` |

## link, unlink

`{"op": "link", "from": "ent_a", "to": "ent_b"}` asserts `same_as`; `unlink` denies it.

## promote

`{"op": "promote", "ref": "$c", "about": ["ent_..."], "about_edges": ["edg_..."]}` makes the
payload's claim a Claim node (id = the claim id, kind = its modality) with `about` edges.

## transition

`{"op": "transition", "node": "evt_...", "status": "completed"}`. Sources that disagree
make the status `contested`.

## redact

`{"op": "redact", "node": "agt_...", "fields": ["name", "identity"]}` or
`{"op": "redact", "claim": "clm_..."}` masks personal data in the graph and the log views.

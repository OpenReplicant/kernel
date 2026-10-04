# Gap queries

Run these with `query_graph` before the interview and again when the topic moves. `$topic`
is the id of the node the interview is about (a process, team or system, from
`lookup_entities`); the scoped queries look two steps around it. Packs add queries for
their own kinds (the bpm pack: roles nobody holds, steps without an owner or next step).

## Contested facts near the topic

Both sides are on record; ask the person neutrally, then ask what would settle it.

```cypher
MATCH (t {id: $topic})-[*0..2]-(n)-[r]-()
WHERE r.belief_status = 'contested'
WITH DISTINCT r
MATCH (a)-[r]->(b)
RETURN r.id, a.name, r.edge, r.kind, b.name, r.valid_from, r.valid_to, r.window_agreed, r.contested_with
```

`window_agreed: false` means the sources agree on the fact but not on its dates.

## Lifecycle statuses sources disagree on

```cypher
MATCH (n) WHERE n.status = 'contested'
RETURN n.id, n.name, n.kind, n.status_options
```

## Facts with a single source near the topic

Worth confirming when they matter (who decides, who approves, what a step depends on).

```cypher
MATCH (t {id: $topic})-[*0..2]-(n)-[r]-()
WHERE r.belief_status = 'accepted' AND r.sources_for = 1
WITH DISTINCT r
MATCH (a)-[r]->(b)
RETURN r.id, a.name, r.edge, r.kind, b.name
```

## People's roles and teams without dates

Who holds a role or belongs to a team changes; ask since when, and whether it still holds.

```cypher
MATCH (a:Agent)-[r]->(b)
WHERE r.edge IN ['implements', 'part_of'] AND r.valid_from IS NULL AND r.valid_to IS NULL
RETURN r.id, a.name, r.edge, b.name
```

## Things nobody has related to anything

```cypher
MATCH (n:Entity) WHERE NOT EXISTS((n)-[]-())
RETURN n.id, n.name, n.kind
```

## Everything around one node

```cypher
MATCH (n {id: $id})-[r]-(m)
RETURN r.id, r.edge, r.from, m.id, m.name, m.kind, r.belief_status, r.valid_from, r.valid_to
```

## Unresolved claims

Statements that did not fit the ontology yet; a question may place them. Use `query_log`
with `{"resolution": "unresolved"}`, adding `source_id` to keep to one source.

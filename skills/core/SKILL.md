---
name: world-model-core
description: >
  Map what people say and write into the World Model Kernel and answer from it with
  receipts. Use whenever the wmk MCP tools are available and a conversation, document or
  question involves facts about people, roles, processes, systems, events, decisions or
  plans: read the graph first, extract one sourced claim at a time with the graph
  operations it justifies, fix rejected writes from their problem documents, and cite
  what each answer sentence relied on.
metadata:
  kernel: ">=0.1 <1.0"
  tools: write, lookup_entities, get_schema_slice, query_graph, query_log, ingest_source, cite
---

# World Model Kernel: core skill

The kernel keeps a log of **claims** (who said what, from which source, when) and a graph
projected from it. You never edit the graph directly: you submit a claim with the
operations it justifies, and the kernel accepts it into the log and the graph in one
commit, or hands it back with the reason. Nothing is deleted; corrections are new claims.

## The loop

1. **Ingest the source.** Call `ingest_source` with the document, file text or
   conversation turn. Keep the returned chunk ids: every reported claim cites one as
   `claim.source`. Known content is not stored twice; you get its chunks back.
2. **Read first.**
   - `get_schema_slice` with the passage: which kinds, edges and rules apply. Use only
     the terms it returns (kinds, kernel edges and their specialisations).
   - `lookup_entities` with every name in the passage (with type and kind when you know
     them). Reuse ids in the `certain` and `high` bands. For `ambiguous` candidates,
     decide: reuse the id, or create a new node with the candidates in `distinct_from`.
   - `query_graph` for what the graph already says about those nodes, when it matters
     (current role holders, existing windows, contested facts).
   - Every read returns `head_offset`. Send the latest one as `read_at_offset`.
3. **Extract one claim at a time.** A claim is one statement as the source makes it.
   - `text`: the statement in one sentence, close to the source's wording.
   - `basis`: `observed` (you or a system saw it), `reported` (someone said or wrote it:
     needs `source`), `inferred` (you derived it).
   - `modality`: `descriptive` (is/was), `predictive` (will), `normative` (should, must,
     policy), `proposed` (a suggestion), `hypothetical` (if/maybe).
   - `confidence`: how firmly the source commits (`low`, `medium`, `high`), not how sure
     you are. Hedged speech ("I think", "usually") is low or medium.
   - `polarity`: `negative` for denials ("Dana no longer approves").
4. **Choose the operations the claim justifies** (details in
   [reference/operations.md](reference/operations.md)):
   - `create` new nodes first, each with a `$ref`; later ops in the same payload use the
     ref, other ops use ids from your reads. Entities need a kind from the slice and,
     in a pack's domain, its namespace (`bpm` for processes, activities, roles).
   - `assert` edges between ids or refs, with `valid_from`/`valid_to` when the source
     dates the fact ("since March 2026" means `valid_from: 2026-03-01`). Resolve
     relative dates against the source's own date, never today's. A document's own
     date ("org chart, January 2026") says when the fact held, not when it began: leave
     `valid_from` out unless the source says when it started.
   - To change an existing fact, assert on its `edge_id`: close it with `valid_to`, or
     deny it with `polarity: negative`. Never recreate it.
   - Goals, rules, requirements and proposals are claims: `promote` them into Claim
     nodes `about` the nodes they concern; relate claims with `supports`, `contradicts`,
     `supersedes`, `refines`, `assumes`.
   - Duplicates you discover later: `link` them (`same_as`); nodes are never merged.
5. **Write** with `write`. On success keep the returned `refs` (your `$refs` mapped to
   ids) and use the returned `offset` as your next `read_at_offset`. The returned
   `edges` show the state each edge is in after your write. `contested` is an outcome,
   not a failure: `contested_with` names an edge another source holds open across yours,
   and `window_agreed: false` means sources disagree on the dates. Your claim is on
   record; do not write it again. Tell the person both sides and what would settle it.
6. **Handle rejections.** A rejected write returns an RFC 9457 problem document with
   `type`, `detail`, the broken `rule`, and `candidates` or `nearest` allowed terms.
   Fix the payload and retry (see [reference/rejections.md](reference/rejections.md)).
   After **two** failed retries, write the claim with `ops: []` and
   `unresolved: {"reason": "..."}`. Nothing true is thrown away for failing to fit.
   A claim that maps to no ontology term at all goes straight to unresolved.
7. **Answer with receipts.** Answer from `query_graph` and `query_log`, then call `cite`
   with each answer sentence and the edge or claim ids it relied on. A sentence that
   relied on nothing in the graph is still cited, with no ids: that marks it untraced.

## Rules of thumb

- One claim per payload; several ops per claim are fine when the sentence justifies
  all of them. Do not bundle unrelated statements.
- Report what the source says, not what you believe. When two sources disagree, write
  both claims as they were made: the kernel marks the fact **contested** and keeps both.
  Never "fix" an older fact to match a newer source.
- Same source correcting itself ("actually it was April"): assert on the same edge from
  the same source; its newer assertion supersedes its older one.
- Single-valued facts (one holder of a role at a time): when the source says someone
  took over, assert the new holder from the handover date and close the previous
  holder's edge with `valid_to` in the same payload.
- Names are not ids. Always look names up; use the ids you get back.
- Keep personal details out of names and props unless the source needs them; identity
  keys (an email for a person) go in `identity`.
- Two clocks: `valid_from`/`valid_to` say when something was true in the world.
  `query_graph` takes `valid_at` (true then) and `known_at_offset` (believed then).

## Conversations

When the person you work with tells you things (an interview, a chat), with their
consent ingest each of their turns as a source: `collection` set to the session id,
`uri` to the turn number, `author` to their agent id (the gateway names it). Claims from
their turns are `reported` and cite the turn's chunk. If they decline capture, do not
ingest their turns; claims you write then have no source and must be `observed` or
`inferred` from your own work.

## Example

Passage (chunk `chk_..._0002`): "Sam took over invoice approval from Dana in March 2026."
After `lookup_entities` found Dana (`agt_01J...DANA`), the role (`ent_01J...ROLE`) and
Dana's edge (`edg_01J...E1`), and nothing for Sam:

```json
{"claim": {"text": "Sam took over invoice approval from Dana in March 2026",
           "source": "chk_..._0002", "basis": "reported", "modality": "descriptive"},
 "read_at_offset": 48210,
 "ops": [{"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam"},
         {"op": "assert", "edge": "implements", "from": "$sam", "to": "ent_01J...ROLE",
          "valid_from": "2026-03-01"},
         {"op": "assert", "edge_id": "edg_01J...E1", "valid_to": "2026-03-01"}]}
```

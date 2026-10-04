# Rejections and how to fix them

| `type` | Means | Fix |
| --- | --- | --- |
| `urn:wmk:rule:types` | Unknown kind or status, or a kind the namespace does not allow | Use one of `nearest`; check the namespace |
| `urn:wmk:rule:domain-range` | The edge cannot connect these nodes | Use one of `nearest` (edges allowed between them) or fix an endpoint |
| `urn:wmk:rule:cardinality` | This source would give a single-valued edge two holders at once | Close the other edge (`edge_id` in `conflicting_edges`) with `valid_to` in the same payload, or deny it |
| `urn:wmk:rule:time` | `valid_from` not before `valid_to`, or an event ending before it starts | Fix the dates |
| `urn:wmk:rule:identity` | Identity key malformed or not declared for that kind | Fix the value; use a key from `nearest` |
| `urn:wmk:rule:provenance` | Basis too weak for this edge, or a reported claim without a source | Cite the chunk; do not infer what must be reported or observed |
| `urn:wmk:write:duplicate` | The node probably exists (`candidates`) | Use the candidate's id; if it is truly different, add it to `distinct_from` (not possible for identity-key matches) |
| `urn:wmk:write:stale-read` | Nodes you touch changed since `read_at_offset` (`changed`) | Read them again, rethink, resubmit with the new `head_offset` |
| `urn:wmk:write:unknown-reference` | An id or `$ref` does not exist (`candidates` may suggest ids for a name) | Look the name up; define refs before using them |
| `urn:wmk:write:invalid-payload` | Malformed payload (`field`, `op_index`) | Fix the field |

Two failed retries on the same claim: write it with `ops: []` and
`"unresolved": {"reason": "...", "rule": "<last rule>"}`.

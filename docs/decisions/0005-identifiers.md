# 0005. Identifiers

Date: 2026-10-04 · Status: accepted

## Decision

- Ids are `<prefix>_<ULID>`: a UUIDv7 (Postgres 18 `uuidv7()`) rendered in Crockford
  base32, so ids sort by creation time. Prefixes: `ent`, `agt`, `clm`, `evt` (nodes by
  type), `edg`, `src`, `cit`, `ans`. Log entries have a UUIDv7 `entry_id`.
- Ids are assigned by the write functions when the entry is logged and are written into
  the resolved operations, so the projection never generates an id.
- A Claim node shares its id with the claim record it was promoted from.
- Assertion ids derive from the claim id and the operation index
  (`asr_<claim ULID>_<index>`); chunk ids from the source id and sequence
  (`chk_<source ULID>_0003`), stable for a source.

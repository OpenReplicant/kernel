# 0006. Redaction in Phase 1 masks the projection and the read paths

Date: 2026-10-04 · Status: accepted

## Context

The design encrypts source content per source and erases by deleting the key. That needs
key management, which Phase 1 does not list.

## Decision

The `redact` operation masks node fields (name, aliases, identity, props, claim text)
in the graph and its AGE mirror, and masks claim text and the created nodes' fields in
the reader views (`claims_view`, `log_entries`, `query_log`). The reader role cannot
select the raw `claims.text`, `log.claim` or `log.ops` columns. Replay reproduces the
masking because redaction is a logged operation.

## Consequences

Redacted data remains in the append-only log tables and source content, visible to the
database owner. Real erasure arrives with per-source encryption keys; until then, do not
put data that must be erasable into a Phase 1 deployment.

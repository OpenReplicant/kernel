-- 95_roles.sql
-- Privileges. The writer may only execute the three write functions; the reader may
-- only select (redaction-aware where text is involved) and call read functions.
-- Nobody but the owner may change a table, and the log tables refuse UPDATE, DELETE
-- and TRUNCATE even for the owner.

SET ROLE kernel_owner;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA kernel FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA kernel FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON kernel.log, kernel.claims, kernel.assertions, kernel.sources, kernel.chunks,
  kernel.cites FROM kernel_writer, kernel_reader;

GRANT USAGE ON SCHEMA kernel TO kernel_writer, kernel_reader;

-- Writer: the three write functions and nothing else.
GRANT EXECUTE ON FUNCTION kernel.write(jsonb, text), kernel.ingest_source(jsonb, text), kernel.cite(jsonb, text)
  TO kernel_writer;

-- Reader: everything but the raw claim text and the raw log payload, which carry text
-- that may be redacted; the views below mask it.
GRANT SELECT ON kernel.sources, kernel.chunks, kernel.assertions, kernel.cites,
  kernel.node_types, kernel.edge_types, kernel.namespaces, kernel.kinds, kernel.edge_kinds, kernel.statuses,
  kernel.rules, kernel.nodes, kernel.edges, kernel.conflicts, kernel.node_touches, kernel.claim_redactions,
  kernel.claims_view, kernel.log_entries, kernel.history, kernel.packs
  TO kernel_reader;
GRANT SELECT (log_offset, entry_id, kind, agent_id, agent_trust, conflicts, read_at_offset, trace_id, span_id,
              recorded_at, belief_version) ON kernel.log TO kernel_reader;
GRANT SELECT (id, log_offset, chunk_id, source_id, source_key, agent_id, basis, modality, polarity, confidence,
              trust, resolution, unresolved, recorded_at, trace_id, span_id) ON kernel.claims TO kernel_reader;

GRANT EXECUTE ON FUNCTION
  kernel.head_offset(),
  kernel.version(),
  kernel.query_log(jsonb),
  kernel.schema_slice(text, text[], int),
  kernel.resolve_candidates(text, text, text, jsonb, vector, int),
  kernel.state_as_of(text[], text[], bigint),
  kernel.edge_state_as_of(text, bigint),
  kernel.node_state_as_of(text, bigint),
  kernel.belief_v1(text, text, bigint),
  kernel.belief_status_v1(numeric, numeric, boolean),
  kernel.counted_assertions(text, text, bigint),
  kernel.mask_ops(jsonb),
  kernel.iso(timestamptz),
  kernel.normalize_name(text),
  kernel.normalize_names(text[]),
  kernel.chunk_text(text, text, int),
  kernel.split_span(text, int, int, int),
  kernel.level_weight(text),
  kernel.basis_weight(text),
  kernel.basis_rank(text)
TO kernel_reader;

RESET ROLE;

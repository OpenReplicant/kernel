-- 95_roles.sql
-- Privileges. The writer may only execute the three write functions; the reader may
-- only select (redaction-aware where text is involved) and call read functions; the
-- eraser may only review and carry out erasures; the approver reads and decides. Nobody but the owner may change a table,
-- and the log tables refuse UPDATE, DELETE and TRUNCATE even for the owner.

SET ROLE kernel_owner;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA kernel FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA kernel FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON kernel.log, kernel.claims, kernel.assertions, kernel.sources, kernel.chunks,
  kernel.cites, kernel.erasures FROM kernel_writer, kernel_reader, kernel_eraser;

GRANT USAGE ON SCHEMA kernel TO kernel_writer, kernel_reader, kernel_eraser;

-- Writer: the three write functions and nothing else.
GRANT EXECUTE ON FUNCTION kernel.write(jsonb, text), kernel.ingest_source(jsonb, text), kernel.cite(jsonb, text)
  TO kernel_writer;

-- Reader: everything but the raw claim text and the raw log payload, which carry text
-- that may be redacted or sealed, and the data keys; the views below open and mask it.
-- kernel.sources and kernel.chunks hold sealed values as ciphertext; sources_view and
-- chunks_view open them.
GRANT SELECT ON kernel.sources, kernel.chunks, kernel.assertions, kernel.cites,
  kernel.node_types, kernel.edge_types, kernel.namespaces, kernel.kinds, kernel.edge_kinds, kernel.statuses,
  kernel.rules, kernel.nodes, kernel.edges, kernel.conflicts, kernel.node_touches, kernel.claim_redactions,
  kernel.claims_view, kernel.sources_view, kernel.chunks_view, kernel.log_entries, kernel.history, kernel.packs,
  kernel.proposals_view
  TO kernel_reader;
GRANT SELECT (log_offset, entry_id, kind, agent_id, agent_trust, conflicts, read_at_offset, trace_id, span_id,
              recorded_at, belief_version) ON kernel.log TO kernel_reader;
GRANT SELECT (id, log_offset, chunk_id, source_id, source_key, agent_id, basis, modality, polarity, confidence,
              trust, resolution, unresolved, recorded_at, trace_id, span_id, run_id, origins, text_key) ON kernel.claims
  TO kernel_reader;

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
  kernel.belief_v2(text, text, bigint),
  kernel.normalize_origin(text),
  kernel.belief_status_v1(numeric, numeric, boolean),
  kernel.counted_assertions(text, text, bigint),
  kernel.mask_ops(jsonb),
  kernel.unseal(text),
  kernel.unseal_json(text),
  kernel.open_op(jsonb),
  kernel.iso(timestamptz),
  kernel.normalize_name(text),
  kernel.normalize_names(text[]),
  kernel.chunk_text(text, text, int),
  kernel.split_span(text, int, int, int),
  kernel.level_weight(text),
  kernel.basis_weight(text),
  kernel.basis_rank(text)
TO kernel_reader;

-- Eraser: review and carry out erasures, nothing else (ADR 0022).
GRANT EXECUTE ON FUNCTION kernel.erasure_scope(text), kernel.erase(jsonb) TO kernel_eraser;

-- Readers see where proposals stand (ADR 0029).
GRANT EXECUTE ON FUNCTION
  kernel.is_instrument(text),
  kernel.proposal_about(text),
  kernel.proposal_approvers(text),
  kernel.approvals_needed(text),
  kernel.systems_of(text),
  kernel.signed_in()
TO kernel_reader;

-- Approver: a signed-in person reads like any reader (the role is granted kernel_reader in
-- 00_extensions.sql) and decides through kernel.decide, nothing else; no gateway login
-- holds it (ADR 0029).
GRANT EXECUTE ON FUNCTION kernel.decide(jsonb), kernel.signed_in_agent(), kernel.signed_in_email() TO kernel_approver;

RESET ROLE;

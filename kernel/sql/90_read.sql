-- 90_read.sql
-- Read helpers for the gateway and UI tools: head offset, redaction-aware views of the
-- log, the history view, log queries, schema slices and as-of state on record time.
-- Nothing here writes.

SET ROLE kernel_owner;

-- Contract: the offset of the newest log entry, 0 for an empty log.
CREATE FUNCTION kernel.head_offset() RETURNS bigint
LANGUAGE sql STABLE AS $$
  SELECT coalesce(max(log_offset), 0) FROM kernel.log
$$;

-- Contract: resolved operations as shown to readers: embeddings dropped and the redacted
-- fields of created nodes masked as they are in the graph.
CREATE FUNCTION kernel.mask_ops(p_ops jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg(
    CASE WHEN o ->> 'op' = 'create' AND n.redacted <> '{}' THEN
      (o - 'embedding')
      || CASE WHEN 'name' = ANY (n.redacted) THEN jsonb_build_object('name', '[redacted]') ELSE '{}' END
      || CASE WHEN 'aliases' = ANY (n.redacted) THEN jsonb_build_object('aliases', '[]'::jsonb) ELSE '{}' END
      || CASE WHEN 'identity' = ANY (n.redacted) THEN jsonb_build_object('identity', '{}'::jsonb) ELSE '{}' END
      || CASE WHEN 'props' = ANY (n.redacted) THEN jsonb_build_object('props', n.props) ELSE '{}' END
    ELSE o - 'embedding' END
    ORDER BY i), '[]')
  FROM jsonb_array_elements(p_ops) WITH ORDINALITY AS x(o, i)
  LEFT JOIN kernel.nodes n ON o ->> 'op' = 'create' AND n.id = o ->> 'id'
$$;

CREATE VIEW kernel.claims_view AS
SELECT c.id, c.log_offset,
       CASE WHEN r.claim_id IS NULL THEN c.text ELSE '[redacted]' END AS text,
       c.chunk_id, c.source_id, c.source_key, c.agent_id, c.basis, c.modality, c.polarity, c.confidence,
       c.trust, c.resolution, c.unresolved, c.recorded_at, c.trace_id, c.span_id, r.claim_id IS NOT NULL AS redacted
FROM kernel.claims c
LEFT JOIN kernel.claim_redactions r ON r.claim_id = c.id;
COMMENT ON VIEW kernel.claims_view IS 'Claims with redacted text masked. Readers use this view, not kernel.claims.';

CREATE VIEW kernel.log_entries AS
SELECT l.log_offset, l.entry_id, l.recorded_at, l.agent_id, l.read_at_offset, l.trace_id, l.span_id,
       c.id AS claim_id, c.text AS claim_text, c.chunk_id, c.source_id, c.source_key, c.basis, c.modality,
       c.polarity, c.confidence, c.trust, c.resolution, c.unresolved, kernel.mask_ops(l.ops) AS ops, l.conflicts
FROM kernel.log l
JOIN kernel.claims_view c ON c.log_offset = l.log_offset;
COMMENT ON VIEW kernel.log_entries IS 'Log entries with their claim, redaction applied.';

CREATE VIEW kernel.history AS
SELECT l.recorded_at, l.log_offset, 'write'::text AS kind, l.entry_id::text AS id, l.agent_id,
       c.id AS ref_id, c.source_id, l.trace_id
FROM kernel.log l JOIN kernel.claims c ON c.log_offset = l.log_offset
UNION ALL
SELECT s.recorded_at, NULL, 'ingest', s.id, s.agent_id, s.id, s.id, s.trace_id
FROM kernel.sources s
UNION ALL
SELECT ct.recorded_at, ct.at_offset, 'cite', ct.id, ct.agent_id, ct.answer_id, NULL, ct.trace_id
FROM kernel.cites ct;
COMMENT ON VIEW kernel.history IS
  'What was known and how it was used: log entries, source ingestions and cite records merged by time.';

-- Contract: log entries matching the filter, newest first unless order is "asc".
-- filter: {"source_id"?, "chunk_id"?, "agent_id"?, "node_id"?, "edge_id"?, "since"?,
--          "until"? (recorded_at), "after_offset"?, "before_offset"?, "resolution"?,
--          "order"?, "limit"? (default 50, at most 500)}
-- Returns {"head_offset", "entries": [...]}.
CREATE FUNCTION kernel.query_log(p_filter jsonb DEFAULT '{}') RETURNS jsonb
LANGUAGE sql STABLE AS $$
  WITH f AS (
    SELECT coalesce(p_filter, '{}') AS f
  ),
  hits AS (
    SELECT e.*
    FROM kernel.log_entries e, f
    WHERE (NOT f.f ? 'source_id' OR e.source_id = f.f ->> 'source_id')
      AND (NOT f.f ? 'chunk_id' OR e.chunk_id = f.f ->> 'chunk_id')
      AND (NOT f.f ? 'agent_id' OR e.agent_id = f.f ->> 'agent_id')
      AND (NOT f.f ? 'resolution' OR e.resolution = f.f ->> 'resolution')
      AND (NOT f.f ? 'since' OR e.recorded_at >= (f.f ->> 'since')::timestamptz)
      AND (NOT f.f ? 'until' OR e.recorded_at < (f.f ->> 'until')::timestamptz)
      AND (NOT f.f ? 'after_offset' OR e.log_offset > (f.f ->> 'after_offset')::bigint)
      AND (NOT f.f ? 'before_offset' OR e.log_offset < (f.f ->> 'before_offset')::bigint)
      AND (NOT f.f ? 'node_id' OR EXISTS (
            SELECT 1 FROM kernel.node_touches t WHERE t.node_id = f.f ->> 'node_id' AND t.log_offset = e.log_offset))
      AND (NOT f.f ? 'edge_id' OR EXISTS (
            SELECT 1 FROM kernel.assertions a
            WHERE a.target_type = 'edge' AND a.target_id = f.f ->> 'edge_id' AND a.log_offset = e.log_offset))
    ORDER BY CASE WHEN f.f ->> 'order' = 'asc' THEN e.log_offset ELSE -e.log_offset END
    LIMIT least(coalesce((p_filter ->> 'limit')::int, 50), 500)
  )
  SELECT jsonb_build_object(
    'head_offset', kernel.head_offset(),
    'entries', coalesce((
      SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'offset', h.log_offset, 'entry_id', h.entry_id, 'recorded_at', kernel.iso(h.recorded_at),
        'agent_id', h.agent_id, 'read_at_offset', h.read_at_offset, 'trace_id', h.trace_id,
        'claim', jsonb_build_object('id', h.claim_id, 'text', h.claim_text, 'source', h.chunk_id,
                                    'source_id', h.source_id, 'basis', h.basis, 'modality', h.modality,
                                    'polarity', CASE h.polarity WHEN 1 THEN 'positive' ELSE 'negative' END,
                                    'confidence', h.confidence, 'trust', h.trust, 'resolution', h.resolution,
                                    'unresolved', h.unresolved),
        'ops', h.ops,
        'conflicts', CASE WHEN h.conflicts <> '[]' THEN h.conflicts END)))
      FROM hits h), '[]'))
$$;

-- Contract: the kinds, edges and rules most relevant to a passage, ranked by full-text
-- match of their name, label and description plus trigram similarity of their name.
-- Node types and their statuses are always included. p_namespaces limits kinds to
-- those allowed in the given namespaces. Returns {"head_offset", "node_types",
-- "namespaces", "kinds", "edges", "rules"}.
CREATE FUNCTION kernel.schema_slice(p_passage text, p_namespaces text[] DEFAULT NULL, p_limit int DEFAULT 12)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  WITH q AS (
    SELECT lower(coalesce(p_passage, '')) AS passage,
           (SELECT CASE WHEN count(*) = 0 THEN NULL
                        ELSE to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) END
            FROM unnest(tsvector_to_array(to_tsvector('english', coalesce(p_passage, '')))) AS lexeme) AS tsq
  ),
  kind_ns AS (
    SELECT k.name,
           ARRAY(SELECT ns.name FROM kernel.namespaces ns
                 WHERE NOT EXISTS (SELECT 1 FROM kernel.rules r WHERE r.category = 'types' AND r.namespace = ns.name)
                    OR EXISTS (SELECT 1 FROM kernel.rules r WHERE r.category = 'types' AND r.namespace = ns.name
                                                            AND r.params -> 'kinds' ? k.name)
                 ORDER BY ns.name) AS namespaces
    FROM kernel.kinds k
  ),
  kinds AS (
    SELECT k.*, kn.namespaces,
           coalesce(ts_rank(to_tsvector('english', replace(k.name, '_', ' ') || ' ' || k.label || ' ' || k.description), q.tsq), 0)
           + 0.3 * word_similarity(replace(k.name, '_', ' '), q.passage) AS score
    FROM kernel.kinds k JOIN kind_ns kn ON kn.name = k.name, q
    WHERE p_namespaces IS NULL OR k.node_type <> 'Entity' OR kn.namespaces && p_namespaces
    ORDER BY score DESC, k.name
    LIMIT p_limit
  ),
  edges AS (
    SELECT e.*,
           coalesce(ts_rank(to_tsvector('english', replace(e.name, '_', ' ') || ' ' || e.label || ' ' || e.description), q.tsq), 0)
           + 0.3 * word_similarity(replace(e.name, '_', ' '), q.passage) AS score
    FROM (
      SELECT name, name AS edge, NULL::text AS specialises, label, description, from_types, to_types FROM kernel.edge_types
      UNION ALL
      SELECT ek.name, ek.edge, ek.edge, ek.label, ek.description, et.from_types, et.to_types
      FROM kernel.edge_kinds ek JOIN kernel.edge_types et ON et.name = ek.edge
    ) e, q
    ORDER BY score DESC, e.name
    LIMIT p_limit
  ),
  rules AS (
    SELECT r.* FROM kernel.rules r
    WHERE r.category IN ('time', 'provenance', 'identity')
       OR (r.category = 'types' AND (p_namespaces IS NULL OR r.namespace = ANY (p_namespaces)))
       OR r.params ->> 'edge' IN (SELECT name FROM edges)
       OR r.params ->> 'kind' IN (SELECT name FROM edges)
  )
  SELECT jsonb_build_object(
    'head_offset', kernel.head_offset(),
    'node_types', (SELECT jsonb_agg(jsonb_build_object(
        'name', nt.name, 'label', nt.label, 'description', nt.description,
        'statuses', (SELECT jsonb_agg(s.status ORDER BY s.status) FROM kernel.statuses s WHERE s.node_type = nt.name),
        'default_status', (SELECT s.status FROM kernel.statuses s WHERE s.node_type = nt.name AND s.is_default))
      ORDER BY nt.name) FROM kernel.node_types nt),
    'namespaces', (SELECT jsonb_agg(jsonb_build_object('name', n.name, 'label', n.label, 'description', n.description)
      ORDER BY n.name) FROM kernel.namespaces n WHERE p_namespaces IS NULL OR n.name = ANY (p_namespaces) OR n.name = 'core'),
    'kinds', (SELECT coalesce(jsonb_agg(jsonb_build_object(
        'name', k.name, 'node_type', k.node_type, 'label', k.label, 'description', k.description,
        'namespaces', to_jsonb(k.namespaces), 'score', round(k.score::numeric, 4)) ORDER BY k.score DESC, k.name), '[]')
      FROM kinds k),
    'edges', (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'name', e.name, 'specialises', e.specialises, 'label', e.label, 'description', e.description,
        'from_types', to_jsonb(e.from_types), 'to_types', to_jsonb(e.to_types), 'score', round(e.score::numeric, 4)))
        ORDER BY e.score DESC, e.name), '[]')
      FROM edges e),
    'rules', (SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'id', r.id, 'category', r.category, 'namespace', r.namespace, 'label', r.label,
        'description', r.description, 'params', r.params)) ORDER BY r.id), '[]')
      FROM rules r))
  FROM q
$$;

-- Contract: an edge's state as believed at p_offset on record time: whether it was known,
-- its belief status (including single-valued conflicts recorded by then), score and window.
CREATE FUNCTION kernel.edge_state_as_of(p_edge_id text, p_offset bigint) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  WITH e AS (SELECT * FROM kernel.edges WHERE id = p_edge_id),
  b AS (SELECT * FROM kernel.belief_v1('edge', p_edge_id, p_offset)),
  conflicted AS (
    SELECT EXISTS (
      SELECT 1 FROM kernel.conflicts c, b,
        LATERAL kernel.belief_v1('edge', CASE WHEN c.edge_a = p_edge_id THEN c.edge_b ELSE c.edge_a END, p_offset) pb
      WHERE (c.edge_a = p_edge_id OR c.edge_b = p_edge_id) AND c.log_offset <= p_offset
        AND b.status NOT IN ('rejected', 'unknown') AND pb.status NOT IN ('rejected', 'unknown')
        AND tstzrange(b.valid_from, b.valid_to, '[)') && tstzrange(pb.valid_from, pb.valid_to, '[)')
    ) AS hit
  )
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'known', e.created_offset <= p_offset,
    'belief_status', CASE WHEN e.created_offset > p_offset THEN 'unknown'
                          WHEN b.status = 'accepted' AND conflicted.hit THEN 'contested' ELSE b.status END,
    'belief_score', b.score,
    'valid_from', kernel.iso(b.valid_from), 'valid_to', kernel.iso(b.valid_to),
    'window_agreed', b.window_agreed))
  FROM e, b, conflicted
$$;

-- Contract: a node's state as believed at p_offset: whether it was known, its lifecycle
-- status and, for Claim nodes, its belief status.
CREATE FUNCTION kernel.node_state_as_of(p_node_id text, p_offset bigint) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_strip_nulls(jsonb_build_object(
    'known', n.created_offset <= p_offset,
    'status', CASE cardinality(s.status_values) WHEN 0 THEN NULL WHEN 1 THEN s.status_values[1] ELSE 'contested' END,
    'belief_status', CASE WHEN n.type = 'Claim' THEN cb.status END,
    'belief_score', CASE WHEN n.type = 'Claim' THEN cb.score END))
  FROM kernel.nodes n,
       LATERAL kernel.belief_v1('status', n.id, p_offset) s,
       LATERAL kernel.belief_v1('claim', n.id, p_offset) cb
  WHERE n.id = p_node_id
$$;

-- Contract: state as of p_offset for many nodes and edges at once: {"nodes": {id: state},
-- "edges": {id: state}}. Unknown ids are left out.
CREATE FUNCTION kernel.state_as_of(p_node_ids text[], p_edge_ids text[], p_offset bigint) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'nodes', (SELECT coalesce(jsonb_object_agg(n.id, kernel.node_state_as_of(n.id, p_offset)), '{}')
              FROM kernel.nodes n WHERE n.id = ANY (p_node_ids)),
    'edges', (SELECT coalesce(jsonb_object_agg(e.id, kernel.edge_state_as_of(e.id, p_offset)), '{}')
              FROM kernel.edges e WHERE e.id = ANY (p_edge_ids)))
$$;

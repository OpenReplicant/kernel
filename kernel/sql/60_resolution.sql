-- 60_resolution.sql
-- The resolution cascade: identity keys -> normalized exact -> pg_trgm -> pgvector.
-- Used by kernel.write on every create and by the lookup_entities tool.
--
-- Bands: certain (identity key match), high (normalized exact match, trigram
-- similarity >= 0.85 or cosine similarity >= 0.93) and ambiguous (trigram >= 0.45 or
-- cosine >= 0.80). kernel.write rejects a create with certain or high candidates
-- (high ones can be waived with distinct_from); the model decides the ambiguous band.

SET ROLE kernel_owner;

-- Contract: ranked existing nodes that may be the node described, best band first,
-- then score. p_type limits to one node type; p_kind, when given, limits name-based
-- stages to that kind (identity keys match across kinds of the same type).
-- p_embedding enables the vector stage. Redacted names never match. Read-only and
-- deterministic for a given state.
CREATE FUNCTION kernel.resolve_candidates(
  p_name text,
  p_type text DEFAULT NULL,
  p_kind text DEFAULT NULL,
  p_identity jsonb DEFAULT '{}',
  p_embedding vector DEFAULT NULL,
  p_limit int DEFAULT 5
)
RETURNS TABLE (node_id text, name text, type text, kind text, namespace text, stage text, score numeric, band text)
LANGUAGE sql STABLE
-- Trigram matches use the % operator at this threshold. Every stage has an index (identity
-- GIN, name btree, alias GIN, trigram GIN).
SET pg_trgm.similarity_threshold = 0.45
AS $$
  -- The normalised name is written as an expression of the parameter in every stage, not
  -- joined from a CTE, so the planner can use the name, alias and trigram indexes.
  WITH hits AS (
    -- 1. identity keys (stored lower-case by kernel.write)
    SELECT n.id, 'identity'::text AS stage, 1.0::numeric AS score, 'certain'::text AS band
    FROM kernel.nodes n, jsonb_each_text(coalesce(p_identity, '{}')) AS k(key, value)
    WHERE (p_type IS NULL OR n.type = p_type)
      AND n.identity @> jsonb_build_object(k.key, lower(k.value))
    UNION ALL
    -- 2. normalized exact, on the name
    SELECT n.id, 'normalized', 1.0, 'high'
    FROM kernel.nodes n
    WHERE kernel.normalize_name(coalesce(p_name, '')) <> ''
      AND (p_type IS NULL OR n.type = p_type) AND (p_kind IS NULL OR n.kind = p_kind)
      AND NOT ('name' = ANY (n.redacted))
      AND n.name_norm = kernel.normalize_name(coalesce(p_name, ''))
    UNION ALL
    -- 2b. normalized exact, on an alias
    SELECT n.id, 'normalized', 1.0, 'high'
    FROM kernel.nodes n
    WHERE kernel.normalize_name(coalesce(p_name, '')) <> ''
      AND (p_type IS NULL OR n.type = p_type) AND (p_kind IS NULL OR n.kind = p_kind)
      AND NOT ('name' = ANY (n.redacted))
      AND n.aliases_norm @> ARRAY[kernel.normalize_name(coalesce(p_name, ''))]
    UNION ALL
    -- 3. trigram similarity on the name (at least 0.45, the threshold set above)
    SELECT t.id, 'trigram', round(t.sim::numeric, 4), CASE WHEN t.sim >= 0.85 THEN 'high' ELSE 'ambiguous' END
    FROM (
      SELECT n.id, similarity(n.name_norm, kernel.normalize_name(coalesce(p_name, ''))) AS sim
      FROM kernel.nodes n
      WHERE kernel.normalize_name(coalesce(p_name, '')) <> ''
        AND (p_type IS NULL OR n.type = p_type) AND (p_kind IS NULL OR n.kind = p_kind)
        AND NOT ('name' = ANY (n.redacted))
        AND n.name_norm % kernel.normalize_name(coalesce(p_name, ''))
    ) t
    UNION ALL
    -- 4. embedding cosine similarity, when both sides have vectors of the same size
    SELECT n.id, 'vector', round((1 - (n.embedding <=> p_embedding))::numeric, 4),
           CASE WHEN 1 - (n.embedding <=> p_embedding) >= 0.93 THEN 'high' ELSE 'ambiguous' END
    FROM kernel.nodes n
    WHERE p_embedding IS NOT NULL AND n.embedding IS NOT NULL
      AND vector_dims(n.embedding) = vector_dims(p_embedding)
      AND (p_type IS NULL OR n.type = p_type) AND (p_kind IS NULL OR n.kind = p_kind)
      AND 1 - (n.embedding <=> p_embedding) >= 0.80
  ),
  best AS (
    SELECT DISTINCT ON (h.id) h.id, h.stage, h.score, h.band
    FROM hits h
    ORDER BY h.id,
             CASE h.band WHEN 'certain' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
             h.score DESC,
             CASE h.stage WHEN 'identity' THEN 0 WHEN 'normalized' THEN 1 WHEN 'trigram' THEN 2 ELSE 3 END
  )
  SELECT n.id, n.name, n.type, n.kind, n.namespace, b.stage, b.score, b.band
  FROM best b JOIN kernel.nodes n ON n.id = b.id
  ORDER BY CASE b.band WHEN 'certain' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, b.score DESC, n.id
  LIMIT p_limit
$$;

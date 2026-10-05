-- 92_erase.sql
-- Erasure (ADR 0022): kernel.erasure_scope shows what erasing a person would destroy and
-- what it would leave; kernel.erase destroys their data keys, re-projects what those keys
-- sealed and records the erasure. The log is never touched. Both run only as kernel_eraser:
-- an operator carrying out an approved request, never an agent or the gateway.

SET ROLE kernel_owner;

-- Contract: the data keys erasing p_subject destroys: the subject's own key (a human
-- agent's fields), the key of every sealed source listing them among its subjects, and the
-- keys of the sealed sources in p_sources. Sorted. Reads only.
CREATE FUNCTION kernel.erasure_keys(p_subject text, p_sources text[] DEFAULT '{}') RETURNS text[]
LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(id ORDER BY id), '{}') FROM kernel.data_keys
  WHERE id = p_subject OR p_subject = ANY (subjects) OR id = ANY (p_sources)
$$;

-- Contract: what erasing p_subject (an agent id) would do, for the approver to review before
-- kernel.erase. Returns {"subject", "name", "keys", "sources": [{"source_id", "title",
-- "collection", "recorded_at"}] (the sealed sources whose keys go), "claims" (how many claims
-- those keys sealed), "nodes" (re-projected as erased), "derived_nodes" (nodes created by
-- those claims that are not sealed, such as an entity named after the person: erasure leaves
-- them, redact them if they identify), "not_covered" (claims in the clear that touch the
-- subject's node: they cite other sources or none, so destroying keys does not reach them;
-- add their sealed sources to the request, or redact or retract them), "erasures" (earlier
-- erasures of this subject)}. Reads only.
CREATE FUNCTION kernel.erasure_scope(p_subject text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_keys text[];
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = p_subject AND type = 'Agent') THEN
    PERFORM kernel.reject('reference', NULL, format('%s is not an agent id', coalesce(p_subject, 'null')),
                          jsonb_build_object('field', 'subject'));
  END IF;
  v_keys := kernel.erasure_keys(p_subject);
  RETURN jsonb_build_object(
    'subject', p_subject,
    'name', (SELECT name FROM kernel.nodes WHERE id = p_subject),
    'keys', to_jsonb(v_keys),
    'sources', (
      SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'source_id', s.id, 'title', s.title, 'collection', s.collection, 'recorded_at', kernel.iso(s.recorded_at)))
             ORDER BY s.recorded_at, s.id), '[]')
      FROM kernel.sources_view s WHERE s.id = ANY (v_keys)),
    'claims', (SELECT count(*) FROM kernel.claims WHERE text_key = ANY (v_keys)),
    'nodes', (SELECT coalesce(jsonb_agg(id ORDER BY id), '[]') FROM kernel.nodes WHERE sealed_key = ANY (v_keys)),
    'derived_nodes', (
      SELECT coalesce(jsonb_agg(jsonb_build_object('node_id', n.id, 'type', n.type, 'kind', n.kind, 'name', n.name)
             ORDER BY n.id), '[]')
      FROM kernel.nodes n JOIN kernel.claims c ON c.id = n.claim_id
      WHERE c.text_key = ANY (v_keys) AND n.sealed_key IS NULL),
    'not_covered', (
      SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'claim_id', v.id, 'source_id', v.source_id, 'agent_id', v.agent_id, 'text', v.text))
             ORDER BY v.log_offset), '[]')
      FROM kernel.claims c JOIN kernel.claims_view v ON v.id = c.id
      WHERE c.log_offset IN (SELECT t.log_offset FROM kernel.node_touches t WHERE t.node_id = p_subject)
        AND (c.text_key IS NULL OR NOT c.text_key = ANY (v_keys))),
    'erasures', (
      SELECT coalesce(jsonb_agg(jsonb_build_object('erasure_id', e.id, 'keys', to_jsonb(e.keys),
                                                   'recorded_at', kernel.iso(e.recorded_at)) ORDER BY e.recorded_at), '[]')
      FROM kernel.erasures e WHERE e.subject = p_subject));
END
$$;

-- Contract: kernel.erase(request) erases a data subject by destroying data keys
-- (kernel.erasure_keys). In one transaction, ordered with writes by the log's append lock,
-- it deletes those keys, re-projects the nodes whose fields they sealed
-- (kernel.erase_nodes) and appends the erasure to kernel.erasures. Nothing in the log
-- changes: what the keys sealed reads as erased from then on, in every view, in the graph
-- and in any replay, while ids, edges, assertions and belief stay. Rejects (WMK01) a
-- subject that is not an agent, requested sources that are not sealed, a request without
-- requested_by and approved_by, and a subject with no keys left.
--
-- request: {"subject": agent id, "sources"?: [sealed source id], "requested_by",
--           "approved_by", "reason"?}
-- Returns {"erasure_id", "subject", "keys", "sources", "nodes", "claims", "at_offset",
--          "recorded_at"}.
CREATE FUNCTION kernel.erase(p_request jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_subject text := p_request ->> 'subject';
  v_extra text[];
  v_keys text[];
  v_sources text[];
  v_nodes text[];
  v_claims bigint;
  v_offset bigint;
  v_at timestamptz;
  v_id text;
  v_bad text;
BEGIN
  IF jsonb_typeof(p_request) IS DISTINCT FROM 'object' THEN
    PERFORM kernel.reject('payload', NULL, 'the request must be a JSON object');
  END IF;
  SELECT string_agg(k, ', ') INTO v_bad FROM jsonb_object_keys(p_request) k
  WHERE k NOT IN ('subject', 'sources', 'requested_by', 'approved_by', 'reason');
  IF v_bad IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown request keys: %s', v_bad));
  END IF;
  FOREACH v_bad IN ARRAY ARRAY['requested_by', 'approved_by'] LOOP
    IF jsonb_typeof(p_request -> v_bad) IS DISTINCT FROM 'string' OR btrim(p_request ->> v_bad) = '' THEN
      PERFORM kernel.reject('payload', NULL,
        format('%s is required: who asked for the erasure and who approved it', v_bad), jsonb_build_object('field', v_bad));
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = v_subject AND type = 'Agent') THEN
    PERFORM kernel.reject('reference', NULL, format('subject %s is not an agent id', coalesce(v_subject, 'null')),
                          jsonb_build_object('field', 'subject'));
  END IF;
  IF p_request ? 'sources' AND (jsonb_typeof(p_request -> 'sources') <> 'array'
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_request -> 'sources') x WHERE jsonb_typeof(x) <> 'string')) THEN
    PERFORM kernel.reject('payload', NULL, 'sources must list source ids', jsonb_build_object('field', 'sources'));
  END IF;
  v_extra := ARRAY(SELECT DISTINCT x FROM jsonb_array_elements_text(coalesce(p_request -> 'sources', '[]')) x ORDER BY 1);
  SELECT string_agg(x, ', ') INTO v_bad FROM unnest(v_extra) x
  WHERE NOT EXISTS (SELECT 1 FROM kernel.sources s WHERE s.id = x AND s.subjects <> '{}');
  IF v_bad IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL,
      format('only sealed sources can be erased; these are not: %s (redact their claims instead)', v_bad),
      jsonb_build_object('field', 'sources'));
  END IF;

  -- No write may seal or open with these keys while they go.
  PERFORM pg_advisory_xact_lock(hashtextextended('kernel.log.append', 0));
  v_keys := kernel.erasure_keys(v_subject, v_extra);
  IF cardinality(v_keys) = 0 THEN
    PERFORM kernel.reject('payload', NULL,
      format('%s has no data keys: nothing sealed about them is left to erase', v_subject),
      jsonb_build_object('field', 'subject'));
  END IF;
  v_sources := ARRAY(SELECT id FROM kernel.sources WHERE id = ANY (v_keys) ORDER BY id);
  SELECT count(*) INTO v_claims FROM kernel.claims WHERE text_key = ANY (v_keys);

  DELETE FROM kernel.data_keys WHERE id = ANY (v_keys);
  v_nodes := kernel.erase_nodes(v_keys);

  v_offset := kernel.head_offset();
  v_at := clock_timestamp();
  v_id := kernel.new_id('era');
  INSERT INTO kernel.erasures (id, subject, keys, sources, nodes, requested_by, approved_by, reason, at_offset,
                               recorded_at)
  VALUES (v_id, v_subject, v_keys, v_sources, v_nodes, btrim(p_request ->> 'requested_by'),
          btrim(p_request ->> 'approved_by'), p_request ->> 'reason', v_offset, v_at);
  RETURN jsonb_build_object('erasure_id', v_id, 'subject', v_subject, 'keys', to_jsonb(v_keys),
                            'sources', to_jsonb(v_sources), 'nodes', to_jsonb(v_nodes), 'claims', v_claims,
                            'at_offset', v_offset, 'recorded_at', kernel.iso(v_at));
END
$$;

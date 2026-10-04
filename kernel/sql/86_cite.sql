-- 86_cite.sql
-- kernel.cite: records which assertions each answer sentence relied on.

SET ROLE kernel_owner;

-- Contract: the assertions that currently count for a target (latest per source),
-- as of p_offset. The assertions an answer relied on when it used that target.
CREATE FUNCTION kernel.counted_assertions(p_target_type text, p_target_id text, p_offset bigint)
RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(id ORDER BY id), '{}') FROM (
    SELECT DISTINCT ON (source_key) id
    FROM kernel.assertions
    WHERE target_type = p_target_type AND target_id = p_target_id AND log_offset <= p_offset
    ORDER BY source_key, log_offset DESC, op_index DESC
  ) counted
$$;

-- Contract: kernel.cite(cite, agent_id) stores one cite record per answer sentence and
-- writes nothing else. Sentences may cite assertion ids, edge ids (resolved to the
-- assertions counting for the edge) and claim ids (the claim's own assertions and
-- those counting for its Claim node). A sentence citing nothing is recorded as
-- untraced. Unknown ids are a 'reference' rejection.
--
-- cite: {"answer_id"?, "sentences": [{"text", "assertions"?, "edges"?, "claims"?}],
--        "trace_id"?, "span_id"?}
-- Returns {"answer_id", "at_offset", "cites": [{"id", "sentence_index", "assertion_ids"}],
--          "traced_share"}.
CREATE FUNCTION kernel.cite(p_cite jsonb, p_agent_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_answer text := p_cite ->> 'answer_id';
  v_head bigint;
  v_at timestamptz := clock_timestamp();
  s jsonb;
  i int;
  v_ids text[];
  v_edges text[];
  v_claims text[];
  v_x text;
  v_keys text;
  v_id text;
  v_out jsonb := '[]';
  v_traced int := 0;
  v_total int := 0;
BEGIN
  IF jsonb_typeof(p_cite) IS DISTINCT FROM 'object' OR jsonb_typeof(p_cite -> 'sentences') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_cite -> 'sentences') = 0 THEN
    PERFORM kernel.reject('payload', NULL, 'sentences must be a non-empty list', jsonb_build_object('field', 'sentences'));
  END IF;
  SELECT string_agg(k, ', ') INTO v_keys FROM jsonb_object_keys(p_cite) k
  WHERE k NOT IN ('answer_id', 'sentences', 'trace_id', 'span_id');
  IF v_keys IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown cite keys: %s', v_keys));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = p_agent_id AND type = 'Agent') THEN
    PERFORM kernel.reject('agent', NULL, format('%s is not an agent', coalesce(p_agent_id, 'null')));
  END IF;
  IF v_answer IS NULL THEN
    v_answer := kernel.new_id('ans');
  ELSIF EXISTS (SELECT 1 FROM kernel.cites WHERE answer_id = v_answer) THEN
    PERFORM kernel.reject('payload', NULL, format('answer %s is already cited; cite records are append-only', v_answer));
  END IF;
  SELECT coalesce(max(log_offset), 0) INTO v_head FROM kernel.log;

  FOR s, i IN SELECT x.value, (x.ordinality - 1)::int FROM jsonb_array_elements(p_cite -> 'sentences') WITH ORDINALITY x LOOP
    IF jsonb_typeof(s) <> 'object' OR jsonb_typeof(s -> 'text') IS DISTINCT FROM 'string' OR btrim(s ->> 'text') = '' THEN
      PERFORM kernel.reject('payload', NULL, format('sentences[%s].text is required', i));
    END IF;
    SELECT string_agg(k, ', ') INTO v_keys FROM jsonb_object_keys(s) k
    WHERE k NOT IN ('text', 'assertions', 'edges', 'claims');
    IF v_keys IS NOT NULL THEN
      PERFORM kernel.reject('payload', NULL, format('sentences[%s] has unknown keys: %s', i, v_keys));
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(ARRAY['assertions', 'edges', 'claims']) k
               WHERE s ? k AND (jsonb_typeof(s -> k) <> 'array' OR EXISTS (
                 SELECT 1 FROM jsonb_array_elements(s -> k) x WHERE jsonb_typeof(x) <> 'string'))) THEN
      PERFORM kernel.reject('payload', NULL, format('sentences[%s]: assertions, edges and claims are lists of ids', i));
    END IF;
    v_ids := ARRAY(SELECT jsonb_array_elements_text(coalesce(s -> 'assertions', '[]')));
    v_edges := ARRAY(SELECT jsonb_array_elements_text(coalesce(s -> 'edges', '[]')));
    v_claims := ARRAY(SELECT jsonb_array_elements_text(coalesce(s -> 'claims', '[]')));
    FOREACH v_x IN ARRAY v_ids LOOP
      IF NOT EXISTS (SELECT 1 FROM kernel.assertions WHERE id = v_x) THEN
        PERFORM kernel.reject('reference', NULL, format('sentences[%s]: %s is not an assertion id', i, v_x));
      END IF;
    END LOOP;
    FOREACH v_x IN ARRAY v_edges LOOP
      IF NOT EXISTS (SELECT 1 FROM kernel.edges WHERE id = v_x) THEN
        PERFORM kernel.reject('reference', NULL, format('sentences[%s]: %s is not an edge id', i, v_x));
      END IF;
      v_ids := v_ids || kernel.counted_assertions('edge', v_x, v_head);
    END LOOP;
    FOREACH v_x IN ARRAY v_claims LOOP
      IF NOT EXISTS (SELECT 1 FROM kernel.claims WHERE id = v_x) THEN
        PERFORM kernel.reject('reference', NULL, format('sentences[%s]: %s is not a claim id', i, v_x));
      END IF;
      v_ids := v_ids || ARRAY(SELECT id FROM kernel.assertions WHERE claim_id = v_x)
                     || kernel.counted_assertions('claim', v_x, v_head);
    END LOOP;
    v_ids := ARRAY(SELECT DISTINCT x FROM unnest(v_ids) x ORDER BY x);
    v_id := kernel.new_id('cit');
    INSERT INTO kernel.cites (id, answer_id, sentence_index, sentence, assertion_ids, edge_ids, claim_ids,
                              at_offset, agent_id, recorded_at, trace_id, span_id)
    VALUES (v_id, v_answer, i, s ->> 'text', v_ids, v_edges, v_claims, v_head, p_agent_id, v_at,
            p_cite ->> 'trace_id', p_cite ->> 'span_id');
    v_out := v_out || jsonb_build_object('id', v_id, 'sentence_index', i, 'assertion_ids', to_jsonb(v_ids));
    v_total := v_total + 1;
    IF cardinality(v_ids) > 0 THEN
      v_traced := v_traced + 1;
    END IF;
  END LOOP;

  RETURN jsonb_build_object('answer_id', v_answer, 'at_offset', v_head, 'cites', v_out,
                            'traced_share', round(v_traced::numeric / v_total, 4));
END
$$;

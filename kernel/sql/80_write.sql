-- 80_write.sql
-- kernel.write: the one way a claim and its graph operations enter the log.
--
-- Steps, in order, in one transaction:
--   1. take the append lock and assign the next offset;
--   2. validate every operation against the ontology rules;
--   3. reject when a touched node changed after read_at_offset (stale read);
--   4. run the resolution cascade on every create;
--   5. append the log entry and apply the graph changes;
--   6. belief follows through deterministic triggers.
-- Rejections abort with SQLSTATE WMK01 (kernel.reject); nothing is written.

SET ROLE kernel_owner;

-- Contract: a node descriptor {id, type, kind, namespace, name, new} for an existing
-- node id or a '$ref' defined earlier in the same payload. Unknown ids are a
-- 'reference' rejection listing resolution candidates for the text given.
CREATE FUNCTION kernel.ref_node(p_value text, p_refs jsonb, p_field text) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  d jsonb;
BEGIN
  IF p_value IS NULL OR p_value = '' THEN
    PERFORM kernel.reject('payload', NULL, format('%s is required', p_field), jsonb_build_object('field', p_field));
  END IF;
  IF left(p_value, 1) = '$' THEN
    d := p_refs -> p_value;
    IF d IS NULL OR d ->> 'type' = 'edge' THEN
      PERFORM kernel.reject('reference', NULL,
        format('%s refers to %s, which no create or promote earlier in this payload defines', p_field, p_value),
        jsonb_build_object('field', p_field));
    END IF;
    RETURN d;
  END IF;
  SELECT jsonb_build_object('id', id, 'type', type, 'kind', kind, 'namespace', namespace, 'name', name, 'new', false)
  INTO d FROM kernel.nodes WHERE id = p_value;
  IF d IS NULL THEN
    PERFORM kernel.reject('reference', NULL,
      format('%s %s is not a node id; look the entity up and use its id, or create it with a $ref', p_field, p_value),
      jsonb_build_object('field', p_field, 'candidates',
        (SELECT coalesce(jsonb_agg(to_jsonb(r)), '[]') FROM kernel.resolve_candidates(p_value) r)));
  END IF;
  RETURN d;
END
$$;

-- Contract: true when a node descriptor matches an endpoint spec {"types"?, "kinds"?}. Pure.
CREATE FUNCTION kernel.endpoint_matches(spec jsonb, d jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT (NOT spec ? 'types' OR spec -> 'types' ? (d ->> 'type'))
     AND (NOT spec ? 'kinds' OR spec -> 'kinds' ? (d ->> 'kind'))
$$;

-- Contract: NULL when an edge (kernel edge plus optional specialisation) may connect the
-- two node descriptors; otherwise {"rule", "detail"} for the first rule it breaks:
-- the kernel node-type domain and range, then domain_range rules in id order. A rule
-- applies when its edge matches, its kind (if any) matches, and it has no namespace
-- or either endpoint is in its namespace.
CREATE FUNCTION kernel.edge_violation(p_edge text, p_kind text, f jsonb, t jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  et kernel.edge_types;
  r kernel.rules;
BEGIN
  SELECT * INTO et FROM kernel.edge_types WHERE name = p_edge;
  IF NOT (f ->> 'type' = ANY (et.from_types)) THEN
    RETURN jsonb_build_object('rule', 'kernel.edge_domain',
      'detail', format('%s cannot start at node type %s (allowed: %s)', p_edge, f ->> 'type', array_to_string(et.from_types, ', ')));
  END IF;
  IF NOT (t ->> 'type' = ANY (et.to_types)) THEN
    RETURN jsonb_build_object('rule', 'kernel.edge_range',
      'detail', format('%s cannot end at node type %s (allowed: %s)', p_edge, t ->> 'type', array_to_string(et.to_types, ', ')));
  END IF;
  FOR r IN
    SELECT * FROM kernel.rules
    WHERE category = 'domain_range' AND params ->> 'edge' = p_edge
      AND (NOT params ? 'kind' OR params ->> 'kind' IS NOT DISTINCT FROM p_kind)
      AND (namespace IS NULL OR namespace = f ->> 'namespace' OR namespace = t ->> 'namespace')
    ORDER BY id
  LOOP
    IF coalesce((r.params ->> 'same_type')::boolean, false)
       AND (f ->> 'type' <> t ->> 'type' OR f ->> 'id' = t ->> 'id') THEN
      RETURN jsonb_build_object('rule', r.id, 'detail', r.description);
    END IF;
    IF r.params ? 'from' AND NOT kernel.endpoint_matches(r.params -> 'from', f) THEN
      RETURN jsonb_build_object('rule', r.id,
        'detail', format('%s: %s is a %s %s. %s', r.label, f ->> 'id', f ->> 'kind', f ->> 'type', r.description));
    END IF;
    IF r.params ? 'to' AND NOT kernel.endpoint_matches(r.params -> 'to', t) THEN
      RETURN jsonb_build_object('rule', r.id,
        'detail', format('%s: %s is a %s %s. %s', r.label, t ->> 'id', t ->> 'kind', t ->> 'type', r.description));
    END IF;
  END LOOP;
  RETURN NULL;
END
$$;

-- Contract: every kernel edge and edge specialisation that may connect the two node
-- descriptors, closest to p_wanted first. Used to name the nearest allowed edges.
CREATE FUNCTION kernel.allowed_edges(f jsonb, t jsonb, p_wanted text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce(jsonb_agg(name ORDER BY similarity(name, coalesce(p_wanted, '')) DESC, name), '[]')
  FROM (
    SELECT et.name FROM kernel.edge_types et WHERE kernel.edge_violation(et.name, NULL, f, t) IS NULL
    UNION ALL
    SELECT ek.name FROM kernel.edge_kinds ek WHERE kernel.edge_violation(ek.edge, ek.name, f, t) IS NULL
  ) allowed
$$;

-- Contract: the existing edge with the same kernel edge, kind, endpoints and props whose
-- window overlaps [p_from, p_to), earliest first; NULL when none. Non-overlapping
-- windows never match, so they never collapse into one edge.
CREATE FUNCTION kernel.match_edge(p_edge text, p_kind text, p_from text, p_to text, p_props jsonb,
                                  p_valid_from timestamptz, p_valid_to timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT id FROM kernel.edges
  WHERE edge = p_edge AND kind IS NOT DISTINCT FROM p_kind AND from_id = p_from AND to_id = p_to
    AND props = coalesce(p_props, '{}')
    AND tstzrange(valid_from, valid_to, '[)') && tstzrange(p_valid_from, p_valid_to, '[)')
  ORDER BY created_offset, id
  LIMIT 1
$$;

-- Contract: rejects with the provenance rule an edge breaks for a claim's basis, if any.
CREATE FUNCTION kernel.check_edge_provenance(p_edge text, p_kind text, p_basis text) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r kernel.rules;
BEGIN
  SELECT * INTO r FROM kernel.rules
  WHERE category = 'provenance' AND params ? 'min_basis'
    AND (params ->> 'edge' = p_edge OR params ->> 'edge' = p_kind)
    AND kernel.basis_rank(p_basis) < kernel.basis_rank(params ->> 'min_basis')
  ORDER BY id LIMIT 1;
  IF FOUND THEN
    PERFORM kernel.reject('provenance', r.id,
      format('%s needs basis %s or stronger; the claim''s basis is %s', coalesce(p_kind, p_edge), r.params ->> 'min_basis', p_basis),
      jsonb_build_object('min_basis', r.params ->> 'min_basis'));
  END IF;
END
$$;

-- Contract: rejects a window that breaks a time rule: valid_from must precede valid_to
-- (core.window_order); edges named by a require_valid_from rule need valid_from.
CREATE FUNCTION kernel.check_window(p_edge text, p_kind text, f jsonb, t jsonb,
                                    p_valid_from timestamptz, p_valid_to timestamptz) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r kernel.rules;
BEGIN
  IF p_valid_from IS NOT NULL AND p_valid_to IS NOT NULL AND p_valid_from >= p_valid_to THEN
    PERFORM kernel.reject('time', 'core.window_order',
      format('valid_from %s is not earlier than valid_to %s', kernel.iso(p_valid_from), kernel.iso(p_valid_to)));
  END IF;
  SELECT * INTO r FROM kernel.rules
  WHERE category = 'time' AND coalesce((params ->> 'require_valid_from')::boolean, false)
    AND (params ->> 'edge' = p_edge OR params ->> 'edge' = p_kind)
    AND (namespace IS NULL OR namespace = f ->> 'namespace' OR namespace = t ->> 'namespace')
  ORDER BY id LIMIT 1;
  IF FOUND AND p_valid_from IS NULL THEN
    PERFORM kernel.reject('time', r.id, format('%s needs valid_from: %s', coalesce(p_kind, p_edge), r.description));
  END IF;
END
$$;

-- Contract: the window a source currently asserts for an edge: its latest assertion's
-- window, or the edge's projected window when the source has not asserted on it.
CREATE FUNCTION kernel.source_window(p_edge_id text, p_source_key text)
RETURNS TABLE (valid_from timestamptz, valid_to timestamptz, polarity smallint)
LANGUAGE sql STABLE AS $$
  SELECT * FROM (
    SELECT a.valid_from, a.valid_to, a.polarity FROM kernel.assertions a
    WHERE a.target_type = 'edge' AND a.target_id = p_edge_id AND a.source_key = p_source_key
    ORDER BY a.log_offset DESC, a.op_index DESC LIMIT 1
  ) s
  UNION ALL
  SELECT e.valid_from, e.valid_to, NULL::smallint FROM kernel.edges e
  WHERE e.id = p_edge_id
    AND NOT EXISTS (SELECT 1 FROM kernel.assertions a
                    WHERE a.target_type = 'edge' AND a.target_id = p_edge_id AND a.source_key = p_source_key)
$$;

-- Contract: after the entry's operations are projected, checks every cardinality rule
-- on the edges this entry asserted positively. Within one source, single-valued edges
-- may not overlap: that is a cardinality rejection naming the conflicting edge. Edges
-- that overlap only because another source asserts them are conflicts, returned as
-- [{"edge_a", "edge_b", "rule"}] for the log entry; belief marks them contested.
CREATE FUNCTION kernel.check_cardinality(entry jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_source text := entry -> 'claim' ->> 'source_key';
  v_edge_id text;
  e kernel.edges;
  r kernel.rules;
  k kernel.nodes;
  other kernel.edges;
  v_key text;
  v_counter text;
  s_vf timestamptz;
  s_vt timestamptz;
  s_pol smallint;
  o_vf timestamptz;
  o_vt timestamptz;
  o_pol smallint;
  v_same text[];
  v_others text[];
  v_conflicts jsonb := '[]';
  c text;
BEGIN
  FOR v_edge_id IN
    SELECT DISTINCT o ->> 'edge_id' FROM jsonb_array_elements(entry -> 'ops') o
    WHERE o ->> 'op' = 'assert' AND o ->> 'target' = 'edge' AND (o ->> 'polarity')::int = 1
    ORDER BY 1
  LOOP
    SELECT * INTO e FROM kernel.edges WHERE id = v_edge_id;
    SELECT sw.valid_from, sw.valid_to, sw.polarity INTO s_vf, s_vt, s_pol FROM kernel.source_window(e.id, v_source) sw;
    CONTINUE WHEN s_pol = -1;
    FOR r IN
      SELECT * FROM kernel.rules
      WHERE category = 'cardinality' AND (params ->> 'edge' = e.edge OR params ->> 'edge' = e.kind)
      ORDER BY id
    LOOP
      v_key := CASE WHEN r.params ->> 'key' = 'from' THEN e.from_id ELSE e.to_id END;
      v_counter := CASE WHEN r.params ->> 'key' = 'from' THEN e.to_id ELSE e.from_id END;
      SELECT * INTO k FROM kernel.nodes WHERE id = v_key;
      CONTINUE WHEN r.params ? 'key_kinds' AND NOT (r.params -> 'key_kinds' ? k.kind);
      CONTINUE WHEN r.namespace IS NOT NULL AND r.namespace <> k.namespace;

      v_same := '{}';
      v_others := '{}';
      FOR other IN
        SELECT * FROM kernel.edges x
        WHERE x.edge = e.edge AND x.id <> e.id
          AND (CASE WHEN r.params ->> 'key' = 'from' THEN x.from_id ELSE x.to_id END) = v_key
          AND (CASE WHEN r.params ->> 'key' = 'from' THEN x.to_id ELSE x.from_id END) <> v_counter
        ORDER BY x.id
      LOOP
        SELECT sw.valid_from, sw.valid_to, sw.polarity INTO o_vf, o_vt, o_pol
        FROM kernel.source_window(other.id, v_source) sw;
        IF o_pol = 1 AND tstzrange(o_vf, o_vt, '[)') && tstzrange(s_vf, s_vt, '[)') THEN
          -- This source asserts the other edge itself: its own view must be consistent.
          v_same := v_same || other.id;
        ELSIF kernel.belief_status_v1(other.belief_for, other.belief_against, other.window_agreed) NOT IN ('rejected', 'unknown')
          AND tstzrange(other.valid_from, other.valid_to, '[)') && tstzrange(e.valid_from, e.valid_to, '[)') THEN
          -- Other sources hold the other edge open across this one: a conflict, not an error.
          v_others := v_others || other.id;
        END IF;
      END LOOP;

      IF cardinality(v_same) + 1 > coalesce((r.params ->> 'max')::int, 1) THEN
        SELECT * INTO other FROM kernel.edges WHERE id = v_same[1];
        SELECT sw.valid_from, sw.valid_to INTO o_vf, o_vt FROM kernel.source_window(other.id, v_source) sw;
        PERFORM kernel.reject('cardinality', r.id,
          format('%s already has %s from %s for %s; in the same payload, close that edge''s window (assert edge_id %s with valid_to) or deny it',
                 v_key, e.edge, CASE WHEN r.params ->> 'key' = 'from' THEN other.to_id ELSE other.from_id END,
                 CASE WHEN o_vt IS NULL THEN coalesce(kernel.iso(greatest(o_vf, s_vf)), 'all time') || ' onward'
                      ELSE kernel.iso(greatest(o_vf, s_vf)) || ' to ' || kernel.iso(o_vt) END,
                 other.id),
          jsonb_build_object('conflicting_edges', to_jsonb(v_same), 'edge_id', e.id));
      END IF;
      IF cardinality(v_others) + cardinality(v_same) + 1 > coalesce((r.params ->> 'max')::int, 1) THEN
        FOREACH c IN ARRAY v_others LOOP
          v_conflicts := v_conflicts || jsonb_build_object('edge_a', least(e.id, c), 'edge_b', greatest(e.id, c), 'rule', r.id);
        END LOOP;
      END IF;
    END LOOP;
  END LOOP;
  RETURN (SELECT coalesce(jsonb_agg(DISTINCT x ORDER BY x), '[]') FROM jsonb_array_elements(v_conflicts) x);
END
$$;

-- Contract: kernel.write(payload, agent_id) validates a claim and the graph operations it
-- justifies, and commits the claim, the log entry and the graph change together, or
-- rejects with SQLSTATE WMK01 and writes nothing.
--
-- payload: {"claim": {"text", "source"?: chunk id, "basis", "modality", "polarity"?,
--           "confidence"?}, "read_at_offset", "ops": [...], "trace_id"?, "span_id"?,
--           "unresolved"?: {"reason", "rule"?}}
-- An empty ops list stores an unresolved claim: text and provenance, no graph change.
-- p_agent_id is the writing agent, set by the gateway, never by the model. It may be
-- NULL only for self-registration: one create of an Agent with "self": true.
--
-- Returns {"offset", "entry_id", "claim_id", "recorded_at", "resolution", "refs",
--          "ops", "conflicts", "ambiguous"}.
CREATE FUNCTION kernel.write(payload jsonb, p_agent_id text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_offset bigint;
  v_at timestamptz;
  v_read bigint;
  c jsonb := payload -> 'claim';
  v_claim_id text;
  v_basis text;
  v_modality text;
  v_polarity smallint;
  v_confidence text;
  v_chunk kernel.chunks;
  v_source kernel.sources;
  v_source_key text;
  v_agent text := p_agent_id;
  v_trust text;
  v_self_count int;
  ops jsonb := coalesce(payload -> 'ops', '[]');
  op jsonb;
  idx int;
  resolved jsonb := '[]';
  refs jsonb := '{}';
  new_edges jsonb := '[]';
  ambiguous jsonb := '[]';
  touched text[] := '{}';
  created text[] := '{}';
  -- per-op scratch
  v_type text;
  v_kind text;
  v_ns text;
  v_name text;
  v_status text;
  v_id text;
  v_ref text;
  v_props jsonb;
  v_identity jsonb;
  v_emb vector;
  v_allowed text[];
  v_rule kernel.rules;
  v_key text;
  v_val text;
  v_start timestamptz;
  v_end timestamptz;
  v_edge text;
  v_ekind text;
  f jsonb;
  t jsonb;
  v_vf timestamptz;
  v_vt timestamptz;
  v_pol smallint;
  v_violation jsonb;
  v_edge_row kernel.edges;
  v_local jsonb;
  v_cand jsonb;
  v_fields text[];
  v_promoted boolean := false;
  v_stale jsonb;
  v_row kernel.log;
  entry jsonb;
  v_conflicts jsonb;
  v_unresolved jsonb;
BEGIN
  -- Shape ------------------------------------------------------------------------------
  IF jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN
    PERFORM kernel.reject('payload', NULL, 'the payload must be a JSON object');
  END IF;
  SELECT string_agg(k, ', ') INTO v_val FROM jsonb_object_keys(payload) k
  WHERE k NOT IN ('claim', 'ops', 'read_at_offset', 'trace_id', 'span_id', 'unresolved');
  IF v_val IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown payload keys: %s', v_val));
  END IF;
  IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR coalesce(btrim(c ->> 'text'), '') = '' THEN
    PERFORM kernel.reject('payload', NULL, 'claim.text is required', jsonb_build_object('field', 'claim.text'));
  END IF;
  SELECT string_agg(k, ', ') INTO v_val FROM jsonb_object_keys(c) k
  WHERE k NOT IN ('text', 'source', 'basis', 'modality', 'polarity', 'confidence');
  IF v_val IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown claim keys: %s', v_val));
  END IF;
  v_basis := c ->> 'basis';
  IF v_basis IS NULL OR v_basis NOT IN ('observed', 'reported', 'inferred') THEN
    PERFORM kernel.reject('payload', NULL, 'claim.basis must be observed, reported or inferred',
                          jsonb_build_object('field', 'claim.basis'));
  END IF;
  v_modality := c ->> 'modality';
  IF v_modality IS NULL OR v_modality NOT IN ('descriptive', 'predictive', 'normative', 'proposed', 'hypothetical') THEN
    PERFORM kernel.reject('payload', NULL,
      'claim.modality must be descriptive, predictive, normative, proposed or hypothetical',
      jsonb_build_object('field', 'claim.modality'));
  END IF;
  v_polarity := CASE coalesce(c ->> 'polarity', 'positive') WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 END;
  IF v_polarity IS NULL THEN
    PERFORM kernel.reject('payload', NULL, 'claim.polarity must be positive or negative',
                          jsonb_build_object('field', 'claim.polarity'));
  END IF;
  v_confidence := kernel.confidence_band(c -> 'confidence');
  IF jsonb_typeof(ops) <> 'array' THEN
    PERFORM kernel.reject('payload', NULL, 'ops must be a list', jsonb_build_object('field', 'ops'));
  END IF;
  IF jsonb_array_length(ops) > 200 THEN
    PERFORM kernel.reject('payload', NULL, 'at most 200 operations per payload; split the claim');
  END IF;
  IF jsonb_typeof(payload -> 'read_at_offset') IS DISTINCT FROM 'number' THEN
    PERFORM kernel.reject('payload', NULL,
      'read_at_offset is required: the offset returned by your last read of the graph',
      jsonb_build_object('field', 'read_at_offset'));
  END IF;
  v_read := (payload ->> 'read_at_offset')::bigint;
  v_unresolved := payload -> 'unresolved';
  IF v_unresolved IS NOT NULL AND jsonb_array_length(ops) > 0 THEN
    PERFORM kernel.reject('payload', NULL, 'an unresolved claim carries no operations');
  END IF;

  -- 1. Append lock and offset ------------------------------------------------------------
  PERFORM pg_advisory_xact_lock(hashtextextended('kernel.log.append', 0));
  SELECT coalesce(max(log_offset), 0) + 1, greatest(clock_timestamp(), max(recorded_at))
  INTO v_offset, v_at FROM kernel.log;
  IF v_read < 0 OR v_read >= v_offset THEN
    PERFORM kernel.reject('payload', NULL,
      format('read_at_offset %s is outside the log (head is %s)', v_read, v_offset - 1),
      jsonb_build_object('field', 'read_at_offset', 'head_offset', v_offset - 1));
  END IF;
  v_claim_id := kernel.new_id('clm');

  -- Writing agent --------------------------------------------------------------------------
  SELECT count(*) INTO v_self_count FROM jsonb_array_elements(ops) o
  WHERE o ->> 'op' = 'create' AND coalesce((o ->> 'self')::boolean, false);
  IF v_agent IS NULL THEN
    IF v_self_count <> 1 THEN
      PERFORM kernel.reject('agent', NULL,
        'no writing agent: only a self-registration (one create of an Agent with "self": true) may omit it');
    END IF;
  ELSE
    IF v_self_count > 0 THEN
      PERFORM kernel.reject('agent', NULL, 'a registered agent cannot self-register again');
    END IF;
    SELECT trust_level INTO v_trust FROM kernel.nodes
    WHERE id = v_agent AND type = 'Agent' AND status IS DISTINCT FROM 'inactive';
    IF v_trust IS NULL THEN
      PERFORM kernel.reject('agent', NULL, format('%s is not an active agent', v_agent));
    END IF;
  END IF;

  -- Claim provenance -------------------------------------------------------------------------
  IF c ->> 'source' IS NOT NULL THEN
    SELECT * INTO v_chunk FROM kernel.chunks WHERE id = c ->> 'source';
    IF v_chunk.id IS NULL THEN
      PERFORM kernel.reject('reference', NULL,
        format('claim.source %s is not a chunk id; ingest the source first and cite one of its chunks', c ->> 'source'),
        jsonb_build_object('field', 'claim.source'));
    END IF;
    SELECT * INTO v_source FROM kernel.sources WHERE id = v_chunk.source_id;
  END IF;
  FOR v_rule IN
    SELECT * FROM kernel.rules
    WHERE category = 'provenance' AND coalesce((params ->> 'requires_source')::boolean, false)
      AND (NOT params ? 'basis' OR params ->> 'basis' = v_basis)
      AND (NOT params ? 'modality' OR params ->> 'modality' = v_modality)
    ORDER BY id
  LOOP
    IF v_chunk.id IS NULL THEN
      PERFORM kernel.reject('provenance', v_rule.id, v_rule.description, jsonb_build_object('field', 'claim.source'));
    END IF;
  END LOOP;
  FOR v_rule IN
    SELECT * FROM kernel.rules
    WHERE category = 'provenance' AND params ? 'min_basis' AND params ? 'modality' AND NOT params ? 'edge'
      AND params ->> 'modality' = v_modality AND kernel.basis_rank(v_basis) < kernel.basis_rank(params ->> 'min_basis')
    ORDER BY id
  LOOP
    PERFORM kernel.reject('provenance', v_rule.id, v_rule.description);
  END LOOP;

  -- 2-4. Operations: validate, resolve references, run the resolution cascade ---------------
  FOR op, idx IN SELECT o.value, (o.ordinality - 1)::int FROM jsonb_array_elements(ops) WITH ORDINALITY AS o LOOP
    IF jsonb_typeof(op) <> 'object' THEN
      PERFORM kernel.reject('payload', NULL, format('ops[%s] must be an object', idx));
    END IF;
    SELECT string_agg(k, ', ') INTO v_val FROM jsonb_object_keys(op) k
    WHERE NOT (k = ANY (CASE op ->> 'op'
      WHEN 'create' THEN ARRAY['op', 'ref', 'type', 'kind', 'namespace', 'name', 'aliases', 'identity', 'props', 'status',
                               'trust_level', 'start', 'end', 'embedding', 'self', 'distinct_from']
      WHEN 'assert' THEN ARRAY['op', 'ref', 'edge', 'kind', 'from', 'to', 'edge_id', 'claim_id', 'props',
                               'valid_from', 'valid_to', 'polarity']
      WHEN 'link' THEN ARRAY['op', 'from', 'to']
      WHEN 'unlink' THEN ARRAY['op', 'from', 'to']
      WHEN 'promote' THEN ARRAY['op', 'ref', 'about', 'about_edges', 'props']
      WHEN 'transition' THEN ARRAY['op', 'node', 'status']
      WHEN 'redact' THEN ARRAY['op', 'node', 'claim', 'fields']
      ELSE ARRAY[]::text[] END));
    IF op ->> 'op' IS NULL OR op ->> 'op' NOT IN ('create', 'assert', 'link', 'unlink', 'promote', 'transition', 'redact') THEN
      PERFORM kernel.reject('payload', NULL,
        format('ops[%s].op must be create, assert, link, unlink, promote, transition or redact', idx),
        jsonb_build_object('op_index', idx));
    END IF;
    IF v_val IS NOT NULL THEN
      PERFORM kernel.reject('payload', NULL, format('ops[%s] (%s) has unknown keys: %s', idx, op ->> 'op', v_val),
                            jsonb_build_object('op_index', idx));
    END IF;
    v_ref := op ->> 'ref';
    IF v_ref IS NOT NULL AND (left(v_ref, 1) <> '$' OR refs ? v_ref) THEN
      PERFORM kernel.reject('payload', NULL, format('ops[%s].ref must start with $ and be unique in the payload', idx),
                            jsonb_build_object('op_index', idx));
    END IF;
    v_pol := CASE coalesce(op ->> 'polarity', CASE v_polarity WHEN 1 THEN 'positive' ELSE 'negative' END)
               WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 END;
    IF v_pol IS NULL THEN
      PERFORM kernel.reject('payload', NULL, format('ops[%s].polarity must be positive or negative', idx),
                            jsonb_build_object('op_index', idx));
    END IF;

    CASE op ->> 'op'

    -- create ---------------------------------------------------------------------------
    WHEN 'create' THEN
      v_type := coalesce(op ->> 'type', 'Entity');
      IF v_type NOT IN ('Entity', 'Agent', 'Event') THEN
        PERFORM kernel.reject('payload', NULL,
          format('ops[%s].type must be Entity, Agent or Event; Claim nodes come from promote', idx),
          jsonb_build_object('op_index', idx));
      END IF;
      v_kind := op ->> 'kind';
      IF NOT EXISTS (SELECT 1 FROM kernel.kinds WHERE name = v_kind AND node_type = v_type) THEN
        PERFORM kernel.reject('types', 'kernel.known_kind',
          format('%s is not a kind of %s', coalesce(v_kind, 'null'), v_type),
          jsonb_build_object('op_index', idx, 'nearest', (
            SELECT jsonb_agg(name ORDER BY similarity(name, coalesce(v_kind, '')) DESC, name)
            FROM (SELECT name FROM kernel.kinds WHERE node_type = v_type) k)));
      END IF;
      v_ns := CASE WHEN v_type = 'Agent' THEN 'core' ELSE coalesce(op ->> 'namespace', 'core') END;
      IF NOT EXISTS (SELECT 1 FROM kernel.namespaces WHERE name = v_ns) THEN
        PERFORM kernel.reject('types', 'kernel.known_namespace', format('%s is not a namespace', v_ns),
          jsonb_build_object('op_index', idx, 'nearest',
            (SELECT jsonb_agg(name ORDER BY similarity(name, v_ns) DESC, name) FROM kernel.namespaces)));
      END IF;
      SELECT * INTO v_rule FROM kernel.rules WHERE category = 'types' AND namespace = v_ns ORDER BY id LIMIT 1;
      IF FOUND AND NOT (v_rule.params -> 'kinds' ? v_kind) THEN
        PERFORM kernel.reject('types', v_rule.id,
          format('namespace %s does not allow kind %s', v_ns, v_kind),
          jsonb_build_object('op_index', idx, 'nearest', (
            SELECT jsonb_agg(k ORDER BY similarity(k, v_kind) DESC, k)
            FROM jsonb_array_elements_text(v_rule.params -> 'kinds') k)));
      END IF;
      v_name := btrim(op ->> 'name');
      IF coalesce(v_name, '') = '' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].name is required', idx), jsonb_build_object('op_index', idx));
      END IF;
      IF op ? 'aliases' AND jsonb_typeof(op -> 'aliases') <> 'array' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].aliases must be a list of names', idx));
      END IF;
      v_props := coalesce(op -> 'props', '{}');
      IF jsonb_typeof(v_props) <> 'object' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].props must be an object', idx));
      END IF;

      -- identity keys
      v_identity := coalesce(op -> 'identity', '{}');
      IF jsonb_typeof(v_identity) <> 'object' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].identity must be an object', idx));
      END IF;
      FOR v_key, v_val IN SELECT * FROM jsonb_each_text(v_identity) LOOP
        SELECT * INTO v_rule FROM kernel.rules
        WHERE category = 'identity' AND params ->> 'node_type' = v_type
          AND (NOT params ? 'kinds' OR params -> 'kinds' ? v_kind) AND params -> 'keys' ? v_key
        ORDER BY id LIMIT 1;
        IF NOT FOUND THEN
          PERFORM kernel.reject('identity', 'kernel.declared_identity',
            format('%s is not an identity key for %s %s', v_key, v_kind, v_type),
            jsonb_build_object('op_index', idx, 'nearest', (
              SELECT coalesce(jsonb_agg(DISTINCT k), '[]') FROM kernel.rules r, jsonb_array_elements_text(r.params -> 'keys') k
              WHERE r.category = 'identity' AND r.params ->> 'node_type' = v_type
                AND (NOT r.params ? 'kinds' OR r.params -> 'kinds' ? v_kind))));
        END IF;
        IF v_rule.params -> 'patterns' ? v_key AND v_val !~ (v_rule.params -> 'patterns' ->> v_key) THEN
          PERFORM kernel.reject('identity', v_rule.id, format('%s is not a valid %s', v_val, v_key),
                                jsonb_build_object('op_index', idx));
        END IF;
      END LOOP;

      -- lifecycle status
      v_status := coalesce(op ->> 'status', (SELECT status FROM kernel.statuses WHERE node_type = v_type AND is_default));
      IF NOT EXISTS (SELECT 1 FROM kernel.statuses WHERE node_type = v_type AND status = v_status) THEN
        PERFORM kernel.reject('types', 'kernel.known_status', format('%s is not a status of %s', v_status, v_type),
          jsonb_build_object('op_index', idx, 'nearest',
            (SELECT jsonb_agg(status ORDER BY status) FROM kernel.statuses WHERE node_type = v_type)));
      END IF;

      -- agents and events
      IF v_type = 'Agent' THEN
        IF coalesce(op ->> 'trust_level', 'medium') NOT IN ('low', 'medium', 'high') THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s].trust_level must be low, medium or high', idx));
        END IF;
      ELSIF op ? 'trust_level' OR op ? 'self' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s]: trust_level and self apply to agents only', idx));
      END IF;
      IF v_type = 'Event' THEN
        v_start := kernel.parse_time(op ->> 'start', format('ops[%s].start', idx));
        v_end := kernel.parse_time(op ->> 'end', format('ops[%s].end', idx));
        IF v_start IS NOT NULL AND v_end IS NOT NULL AND v_end < v_start THEN
          PERFORM kernel.reject('time', 'core.event_order',
            format('event end %s is earlier than its start %s', kernel.iso(v_end), kernel.iso(v_start)),
            jsonb_build_object('op_index', idx));
        END IF;
        v_props := v_props || jsonb_strip_nulls(jsonb_build_object('start', kernel.iso(v_start), 'end', kernel.iso(v_end)));
      ELSIF op ? 'start' OR op ? 'end' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s]: start and end apply to events only', idx));
      END IF;

      -- 4. resolution cascade
      v_emb := NULL;
      IF op ? 'embedding' THEN
        BEGIN
          v_emb := (op ->> 'embedding')::vector;
        EXCEPTION WHEN OTHERS THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s].embedding must be a list of numbers', idx));
        END;
      END IF;
      SELECT r.value INTO v_local FROM jsonb_each(refs) r
      WHERE r.value ->> 'type' = v_type AND r.value ->> 'kind' = v_kind
        AND kernel.normalize_name(r.value ->> 'name') = kernel.normalize_name(v_name)
      LIMIT 1;
      IF v_local IS NOT NULL THEN
        PERFORM kernel.reject('duplicate', 'kernel.resolution',
          format('%s is created twice in this payload', v_name), jsonb_build_object('op_index', idx));
      END IF;
      SELECT coalesce(jsonb_agg(to_jsonb(r)), '[]') INTO v_cand
      FROM kernel.resolve_candidates(v_name, v_type, v_kind, v_identity, v_emb, 5) r;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_cand) x WHERE x ->> 'band' = 'certain') THEN
        PERFORM kernel.reject('duplicate', 'kernel.resolution',
          format('%s matches an existing %s by identity key; use its id', v_name, v_type),
          jsonb_build_object('op_index', idx, 'candidates', v_cand));
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_cand) x
                 WHERE x ->> 'band' = 'high' AND NOT coalesce(op -> 'distinct_from', '[]') ? (x ->> 'node_id')) THEN
        PERFORM kernel.reject('duplicate', 'kernel.resolution',
          format('%s is probably an existing %s; use its id, or list it in distinct_from if it is a different one', v_name, v_type),
          jsonb_build_object('op_index', idx, 'candidates', v_cand));
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_cand) x WHERE x ->> 'band' = 'ambiguous') THEN
        ambiguous := ambiguous || jsonb_build_object('op_index', idx, 'name', v_name, 'candidates',
          (SELECT jsonb_agg(x) FROM jsonb_array_elements(v_cand) x WHERE x ->> 'band' = 'ambiguous'));
      END IF;

      v_id := kernel.new_id((SELECT id_prefix FROM kernel.node_types WHERE name = v_type));
      IF coalesce((op ->> 'self')::boolean, false) THEN
        v_agent := v_id;
        v_trust := coalesce(op ->> 'trust_level', 'medium');
      END IF;
      created := created || v_id;
      IF v_ref IS NOT NULL THEN
        refs := refs || jsonb_build_object(v_ref, jsonb_build_object('id', v_id, 'type', v_type, 'kind', v_kind,
                                                                      'namespace', v_ns, 'name', v_name, 'new', true));
      ELSE
        refs := refs || jsonb_build_object('$#' || idx, jsonb_build_object('id', v_id, 'type', v_type, 'kind', v_kind,
                                                                            'namespace', v_ns, 'name', v_name, 'new', true));
      END IF;
      resolved := resolved || jsonb_strip_nulls(jsonb_build_object(
        'op', 'create', 'id', v_id, 'ref', v_ref, 'type', v_type, 'kind', v_kind, 'namespace', v_ns, 'name', v_name,
        'aliases', coalesce(op -> 'aliases', '[]'), 'identity', v_identity, 'props', v_props, 'status', v_status,
        'trust_level', CASE WHEN v_type = 'Agent' THEN coalesce(op ->> 'trust_level', 'medium') END,
        'embedding', CASE WHEN v_emb IS NOT NULL THEN (v_emb::text)::jsonb END,
        'self', CASE WHEN coalesce((op ->> 'self')::boolean, false) THEN true END,
        'distinct_from', op -> 'distinct_from'));

    -- assert ---------------------------------------------------------------------------
    WHEN 'assert' THEN
      IF op ? 'claim_id' THEN
        f := kernel.ref_node(op ->> 'claim_id', refs, format('ops[%s].claim_id', idx));
        IF f ->> 'type' <> 'Claim' THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s].claim_id %s is not a Claim node', idx, f ->> 'id'));
        END IF;
        IF NOT (f ->> 'new')::boolean THEN
          touched := touched || (f ->> 'id');
        END IF;
        resolved := resolved || jsonb_build_object('op', 'assert', 'target', 'claim', 'claim_node', f ->> 'id',
                                                   'polarity', v_pol);
        CONTINUE;
      END IF;

      IF op ? 'edge_id' THEN
        -- An assertion on an existing edge: support, denial or a validity change.
        v_local := NULL;
        IF left(op ->> 'edge_id', 1) = '$' THEN
          SELECT x INTO v_local FROM jsonb_array_elements(new_edges) x WHERE x ->> 'ref' = op ->> 'edge_id';
        ELSE
          SELECT x INTO v_local FROM jsonb_array_elements(new_edges) x WHERE x ->> 'edge_id' = op ->> 'edge_id';
        END IF;
        IF v_local IS NOT NULL THEN
          v_id := v_local ->> 'edge_id';
          v_edge := v_local ->> 'edge';
          v_ekind := v_local ->> 'kind';
          v_edge_row := NULL;
          v_edge_row.id := v_id;
          v_edge_row.from_id := v_local ->> 'from';
          v_edge_row.to_id := v_local ->> 'to';
          v_edge_row.props := v_local -> 'props';
          v_edge_row.valid_from := (v_local ->> 'valid_from')::timestamptz;
          v_edge_row.valid_to := (v_local ->> 'valid_to')::timestamptz;
        ELSE
          SELECT * INTO v_edge_row FROM kernel.edges WHERE id = op ->> 'edge_id';
          IF v_edge_row.id IS NULL THEN
            PERFORM kernel.reject('reference', NULL, format('ops[%s].edge_id %s is not an edge id', idx, op ->> 'edge_id'),
                                  jsonb_build_object('op_index', idx));
          END IF;
          v_id := v_edge_row.id;
          v_edge := v_edge_row.edge;
          v_ekind := v_edge_row.kind;
          touched := touched || v_edge_row.from_id || v_edge_row.to_id;
        END IF;
        IF op ? 'edge' OR op ? 'from' OR op ? 'to' OR op ? 'kind' OR op ? 'props' THEN
          PERFORM kernel.reject('payload', NULL,
            format('ops[%s]: an assertion on edge_id takes only valid_from, valid_to and polarity', idx));
        END IF;
        -- Unspecified window fields keep what this source asserted, else the edge's window.
        SELECT sw.valid_from, sw.valid_to INTO v_vf, v_vt FROM kernel.source_window(v_id, coalesce(
          CASE WHEN v_source.id IS NOT NULL THEN coalesce(v_source.collection, v_source.id) END,
          'agent:' || v_agent)) sw;
        IF v_local IS NOT NULL THEN
          v_vf := v_edge_row.valid_from;
          v_vt := v_edge_row.valid_to;
        END IF;
        IF op ? 'valid_from' THEN
          v_vf := kernel.parse_time(op ->> 'valid_from', format('ops[%s].valid_from', idx));
        END IF;
        IF op ? 'valid_to' THEN
          v_vt := kernel.parse_time(op ->> 'valid_to', format('ops[%s].valid_to', idx));
        END IF;
        PERFORM kernel.check_window(v_edge, v_ekind, '{}', '{}', v_vf, v_vt);
        PERFORM kernel.check_edge_provenance(v_edge, v_ekind, v_basis);
        resolved := resolved || (jsonb_strip_nulls(jsonb_build_object(
          'op', 'assert', 'target', 'edge', 'edge_id', v_id, 'new_edge', false, 'edge', v_edge, 'kind', v_ekind,
          'from', v_edge_row.from_id, 'to', v_edge_row.to_id, 'polarity', v_pol))
          || jsonb_build_object('valid_from', to_jsonb(v_vf), 'valid_to', to_jsonb(v_vt)));
        CONTINUE;
      END IF;

      -- A new edge fact: edge, from, to and an optional window.
      v_edge := op ->> 'edge';
      v_ekind := op ->> 'kind';
      IF EXISTS (SELECT 1 FROM kernel.edge_types WHERE name = v_edge) THEN
        IF v_ekind IS NOT NULL AND NOT EXISTS (SELECT 1 FROM kernel.edge_kinds WHERE name = v_ekind AND edge = v_edge) THEN
          PERFORM kernel.reject('domain_range', 'kernel.known_edge',
            format('%s is not a specialisation of %s', v_ekind, v_edge),
            jsonb_build_object('op_index', idx, 'nearest',
              (SELECT coalesce(jsonb_agg(name ORDER BY name), '[]') FROM kernel.edge_kinds WHERE edge = v_edge)));
        END IF;
      ELSIF EXISTS (SELECT 1 FROM kernel.edge_kinds WHERE name = v_edge) THEN
        IF v_ekind IS NOT NULL AND v_ekind <> v_edge THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s]: edge %s already names a kind', idx, v_edge));
        END IF;
        v_ekind := v_edge;
        SELECT edge INTO v_edge FROM kernel.edge_kinds WHERE name = v_ekind;
      ELSE
        PERFORM kernel.reject('domain_range', 'kernel.known_edge',
          format('%s is not a kernel edge or an edge specialisation', coalesce(v_edge, 'null')),
          jsonb_build_object('op_index', idx, 'nearest', (
            SELECT jsonb_agg(name ORDER BY similarity(name, coalesce(v_edge, '')) DESC, name)
            FROM (SELECT name FROM kernel.edge_types UNION ALL SELECT name FROM kernel.edge_kinds) e)));
      END IF;
      f := kernel.ref_node(op ->> 'from', refs, format('ops[%s].from', idx));
      t := kernel.ref_node(op ->> 'to', refs, format('ops[%s].to', idx));
      IF v_edge = 'same_as' AND f ->> 'id' > t ->> 'id' THEN
        SELECT t, f INTO f, t;
      END IF;
      v_violation := kernel.edge_violation(v_edge, v_ekind, f, t);
      IF v_violation IS NOT NULL THEN
        PERFORM kernel.reject('domain_range', v_violation ->> 'rule', v_violation ->> 'detail',
          jsonb_build_object('op_index', idx, 'nearest', kernel.allowed_edges(f, t, coalesce(v_ekind, v_edge))));
      END IF;
      v_vf := kernel.parse_time(op ->> 'valid_from', format('ops[%s].valid_from', idx));
      v_vt := kernel.parse_time(op ->> 'valid_to', format('ops[%s].valid_to', idx));
      PERFORM kernel.check_window(v_edge, v_ekind, f, t, v_vf, v_vt);
      PERFORM kernel.check_edge_provenance(v_edge, v_ekind, v_basis);
      v_props := coalesce(op -> 'props', '{}');
      IF jsonb_typeof(v_props) <> 'object' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].props must be an object', idx));
      END IF;

      -- Same edge if the same fact with an overlapping window exists, here or in the graph.
      SELECT x ->> 'edge_id' INTO v_id FROM jsonb_array_elements(new_edges) x
      WHERE x ->> 'edge' = v_edge AND x ->> 'kind' IS NOT DISTINCT FROM v_ekind
        AND x ->> 'from' = f ->> 'id' AND x ->> 'to' = t ->> 'id' AND x -> 'props' = v_props
        AND tstzrange((x ->> 'valid_from')::timestamptz, (x ->> 'valid_to')::timestamptz, '[)') && tstzrange(v_vf, v_vt, '[)')
      LIMIT 1;
      IF v_id IS NULL THEN
        v_id := kernel.match_edge(v_edge, v_ekind, f ->> 'id', t ->> 'id', v_props, v_vf, v_vt);
        IF v_id IS NULL THEN
          v_id := kernel.new_id('edg');
          new_edges := new_edges || jsonb_build_object('edge_id', v_id, 'ref', v_ref, 'edge', v_edge, 'kind', v_ekind,
            'from', f ->> 'id', 'to', t ->> 'id', 'props', v_props, 'valid_from', v_vf, 'valid_to', v_vt);
          resolved := resolved || (jsonb_strip_nulls(jsonb_build_object('op', 'assert', 'target', 'edge', 'edge_id', v_id,
            'new_edge', true, 'ref', v_ref, 'edge', v_edge, 'kind', v_ekind, 'from', f ->> 'id', 'to', t ->> 'id',
            'props', v_props, 'polarity', v_pol))
            || jsonb_build_object('valid_from', to_jsonb(v_vf), 'valid_to', to_jsonb(v_vt)));
        ELSE
          resolved := resolved || (jsonb_strip_nulls(jsonb_build_object('op', 'assert', 'target', 'edge', 'edge_id', v_id,
            'new_edge', false, 'ref', v_ref, 'edge', v_edge, 'kind', v_ekind, 'from', f ->> 'id', 'to', t ->> 'id',
            'props', v_props, 'polarity', v_pol))
            || jsonb_build_object('valid_from', to_jsonb(v_vf), 'valid_to', to_jsonb(v_vt)));
        END IF;
      ELSE
        resolved := resolved || (jsonb_strip_nulls(jsonb_build_object('op', 'assert', 'target', 'edge', 'edge_id', v_id,
          'new_edge', false, 'ref', v_ref, 'edge', v_edge, 'kind', v_ekind, 'from', f ->> 'id', 'to', t ->> 'id',
          'props', v_props, 'polarity', v_pol))
          || jsonb_build_object('valid_from', to_jsonb(v_vf), 'valid_to', to_jsonb(v_vt)));
      END IF;
      IF v_ref IS NOT NULL THEN
        refs := refs || jsonb_build_object(v_ref, jsonb_build_object('id', v_id, 'type', 'edge'));
      END IF;
      IF NOT (f ->> 'new')::boolean THEN touched := touched || (f ->> 'id'); END IF;
      IF NOT (t ->> 'new')::boolean THEN touched := touched || (t ->> 'id'); END IF;

    -- link / unlink (same_as) --------------------------------------------------------------
    WHEN 'link', 'unlink' THEN
      f := kernel.ref_node(op ->> 'from', refs, format('ops[%s].from', idx));
      t := kernel.ref_node(op ->> 'to', refs, format('ops[%s].to', idx));
      IF f ->> 'id' > t ->> 'id' THEN
        SELECT t, f INTO f, t;
      END IF;
      v_violation := kernel.edge_violation('same_as', NULL, f, t);
      IF v_violation IS NOT NULL THEN
        PERFORM kernel.reject('domain_range', v_violation ->> 'rule', v_violation ->> 'detail',
                              jsonb_build_object('op_index', idx));
      END IF;
      SELECT x ->> 'edge_id' INTO v_id FROM jsonb_array_elements(new_edges) x
      WHERE x ->> 'edge' = 'same_as' AND x ->> 'from' = f ->> 'id' AND x ->> 'to' = t ->> 'id' LIMIT 1;
      IF v_id IS NULL THEN
        v_id := kernel.match_edge('same_as', NULL, f ->> 'id', t ->> 'id', '{}', NULL, NULL);
      END IF;
      IF v_id IS NULL AND op ->> 'op' = 'unlink' THEN
        PERFORM kernel.reject('reference', NULL,
          format('ops[%s]: there is no same_as link between %s and %s to unlink', idx, f ->> 'id', t ->> 'id'),
          jsonb_build_object('op_index', idx));
      END IF;
      IF v_id IS NULL THEN
        v_id := kernel.new_id('edg');
        new_edges := new_edges || jsonb_build_object('edge_id', v_id, 'edge', 'same_as', 'kind', NULL,
          'from', f ->> 'id', 'to', t ->> 'id', 'props', '{}'::jsonb, 'valid_from', NULL, 'valid_to', NULL);
        resolved := resolved || jsonb_build_object('op', op ->> 'op', 'edge_id', v_id, 'new_edge', true,
                                                   'from', f ->> 'id', 'to', t ->> 'id', 'polarity', 1);
      ELSE
        resolved := resolved || jsonb_build_object('op', op ->> 'op', 'edge_id', v_id, 'new_edge', false,
          'from', f ->> 'id', 'to', t ->> 'id', 'polarity', CASE op ->> 'op' WHEN 'link' THEN 1 ELSE -1 END);
      END IF;
      IF NOT (f ->> 'new')::boolean THEN touched := touched || (f ->> 'id'); END IF;
      IF NOT (t ->> 'new')::boolean THEN touched := touched || (t ->> 'id'); END IF;

    -- promote ----------------------------------------------------------------------------
    WHEN 'promote' THEN
      IF v_promoted THEN
        PERFORM kernel.reject('payload', NULL, 'a payload promotes its claim at most once');
      END IF;
      v_promoted := true;
      IF op ? 'about_edges' AND (jsonb_typeof(op -> 'about_edges') <> 'array'
         OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(op -> 'about_edges') x
                    WHERE NOT EXISTS (SELECT 1 FROM kernel.edges e WHERE e.id = x))) THEN
        PERFORM kernel.reject('reference', NULL, format('ops[%s].about_edges must list existing edge ids', idx));
      END IF;
      f := jsonb_build_object('id', v_claim_id, 'type', 'Claim', 'kind', v_modality, 'namespace', 'core',
                              'name', left(c ->> 'text', 120), 'new', true);
      refs := refs || jsonb_build_object(coalesce(v_ref, '$#' || idx), f);
      resolved := resolved || jsonb_strip_nulls(jsonb_build_object('op', 'promote', 'ref', v_ref, 'node_id', v_claim_id,
        'about_edges', op -> 'about_edges', 'props', op -> 'props'));
      IF op ? 'about' AND jsonb_typeof(op -> 'about') <> 'array' THEN
        PERFORM kernel.reject('payload', NULL, format('ops[%s].about must be a list of node ids', idx));
      END IF;
      FOR v_val IN SELECT jsonb_array_elements_text(coalesce(op -> 'about', '[]')) LOOP
        t := kernel.ref_node(v_val, refs, format('ops[%s].about', idx));
        v_id := kernel.new_id('edg');
        new_edges := new_edges || jsonb_build_object('edge_id', v_id, 'edge', 'about', 'kind', NULL,
          'from', v_claim_id, 'to', t ->> 'id', 'props', '{}'::jsonb, 'valid_from', NULL, 'valid_to', NULL);
        resolved := resolved || (jsonb_build_object('op', 'assert', 'target', 'edge', 'edge_id', v_id, 'new_edge', true,
          'edge', 'about', 'from', v_claim_id, 'to', t ->> 'id', 'props', '{}'::jsonb, 'polarity', 1)
          || jsonb_build_object('valid_from', NULL, 'valid_to', NULL));
        IF NOT (t ->> 'new')::boolean THEN touched := touched || (t ->> 'id'); END IF;
      END LOOP;

    -- transition -------------------------------------------------------------------------
    WHEN 'transition' THEN
      f := kernel.ref_node(op ->> 'node', refs, format('ops[%s].node', idx));
      IF NOT EXISTS (SELECT 1 FROM kernel.statuses WHERE node_type = f ->> 'type' AND status = op ->> 'status') THEN
        PERFORM kernel.reject('types', 'kernel.known_status',
          format('%s is not a status of %s', coalesce(op ->> 'status', 'null'), f ->> 'type'),
          jsonb_build_object('op_index', idx, 'nearest',
            (SELECT jsonb_agg(status ORDER BY status) FROM kernel.statuses WHERE node_type = f ->> 'type')));
      END IF;
      IF NOT (f ->> 'new')::boolean THEN touched := touched || (f ->> 'id'); END IF;
      resolved := resolved || jsonb_build_object('op', 'transition', 'node', f ->> 'id', 'status', op ->> 'status');

    -- redact -----------------------------------------------------------------------------
    WHEN 'redact' THEN
      IF op ? 'claim' THEN
        IF op ? 'node' OR op ? 'fields' THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s]: redact either a claim or a node''s fields', idx));
        END IF;
        IF NOT EXISTS (SELECT 1 FROM kernel.claims WHERE id = op ->> 'claim') THEN
          PERFORM kernel.reject('reference', NULL, format('ops[%s].claim %s is not a claim id', idx, op ->> 'claim'));
        END IF;
        IF EXISTS (SELECT 1 FROM kernel.nodes WHERE id = op ->> 'claim') THEN
          touched := touched || (op ->> 'claim');
          resolved := resolved || jsonb_build_object('op', 'redact', 'claim', op ->> 'claim', 'node', op ->> 'claim');
        ELSE
          resolved := resolved || jsonb_build_object('op', 'redact', 'claim', op ->> 'claim');
        END IF;
      ELSE
        f := kernel.ref_node(op ->> 'node', refs, format('ops[%s].node', idx));
        IF (f ->> 'new')::boolean THEN
          PERFORM kernel.reject('payload', NULL, format('ops[%s]: redact applies to existing nodes', idx));
        END IF;
        v_fields := ARRAY(SELECT jsonb_array_elements_text(coalesce(op -> 'fields', '["name", "aliases", "identity", "props", "text"]')));
        IF v_fields = '{}' OR NOT v_fields <@ ARRAY['name', 'aliases', 'identity', 'props', 'text'] THEN
          PERFORM kernel.reject('payload', NULL,
            format('ops[%s].fields must list some of name, aliases, identity, props, text', idx));
        END IF;
        touched := touched || (f ->> 'id');
        resolved := resolved || jsonb_build_object('op', 'redact', 'node', f ->> 'id', 'fields', to_jsonb(v_fields));
      END IF;
    END CASE;
  END LOOP;

  -- 3. Stale read: a touched node changed after read_at_offset by another agent ------------
  SELECT jsonb_agg(DISTINCT jsonb_build_object('node_id', node_id, 'offset', log_offset)) INTO v_stale
  FROM kernel.node_touches
  WHERE node_id = ANY (touched) AND log_offset > v_read AND agent_id <> v_agent;
  IF v_stale IS NOT NULL THEN
    PERFORM kernel.reject('stale', 'kernel.stale_read',
      format('nodes you touch changed after offset %s; read them again and resubmit', v_read),
      jsonb_build_object('changed', v_stale, 'head_offset', v_offset - 1));
  END IF;

  -- 5. Project, check cardinality, append the log entry, apply conflicts --------------------
  v_source_key := CASE WHEN v_source.id IS NOT NULL THEN coalesce(v_source.collection, v_source.id)
                       ELSE 'agent:' || v_agent END;
  v_row := ROW(
    v_offset, uuidv7(), 'write', v_agent, v_trust,
    jsonb_strip_nulls(jsonb_build_object(
      'id', v_claim_id, 'text', c ->> 'text', 'chunk_id', v_chunk.id, 'source_id', v_source.id,
      'source_key', v_source_key, 'basis', v_basis, 'modality', v_modality, 'polarity', v_polarity,
      'confidence', v_confidence,
      'resolution', CASE WHEN jsonb_array_length(resolved) = 0 THEN 'unresolved' ELSE 'resolved' END,
      'unresolved', CASE WHEN jsonb_array_length(resolved) = 0 THEN coalesce(v_unresolved, '{}') END)),
    resolved, '[]'::jsonb, v_read, payload ->> 'trace_id', payload ->> 'span_id', v_at, 1
  )::kernel.log;
  entry := to_jsonb(v_row);
  PERFORM kernel.project_ops(entry);
  v_conflicts := kernel.check_cardinality(entry);
  v_row.conflicts := v_conflicts;
  INSERT INTO kernel.log VALUES (v_row.*);
  PERFORM kernel.project_conflicts(to_jsonb(v_row));

  RETURN jsonb_build_object(
    'offset', v_offset,
    'entry_id', v_row.entry_id,
    'claim_id', v_claim_id,
    'agent_id', v_agent,
    'recorded_at', kernel.iso(v_at),
    'resolution', entry -> 'claim' ->> 'resolution',
    'refs', (SELECT coalesce(jsonb_object_agg(k, v ->> 'id'), '{}') FROM jsonb_each(refs) AS r(k, v) WHERE k NOT LIKE '$#%'),
    'ops', (SELECT coalesce(jsonb_agg(o - 'embedding'), '[]') FROM jsonb_array_elements(resolved) o),
    'conflicts', v_conflicts,
    'ambiguous', ambiguous);
END
$$;

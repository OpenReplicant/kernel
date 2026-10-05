-- 70_projection.sql
-- Projection: applies one log entry to the claim and assertion records and the graph.
-- Deterministic: every identifier, timestamp and decision is read from the entry.
-- No now(), no randomness, no network. Replaying the log reproduces the graph exactly.

SET ROLE kernel_owner;

-- Contract: records that a log entry changed a node or an edge at it. Idempotent.
CREATE FUNCTION kernel.touch(p_node_id text, p_offset bigint, p_agent_id text)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO kernel.node_touches (node_id, log_offset, agent_id)
  VALUES (p_node_id, p_offset, p_agent_id)
  ON CONFLICT DO NOTHING
$$;

-- Contract: a logged operation with its sealed fields opened (ADR 0022). A create of a
-- human agent carries its name, aliases, identity, props and embedding as one sealed JSON
-- document; they come back from it, or, once its key has been destroyed, as the erased
-- placeholders: name '[erased]' and no aliases, identity, props or embedding. Any other
-- operation comes back unchanged. Deterministic for a given set of keys.
CREATE FUNCTION kernel.open_op(op jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN op ? 'sealed'
              THEN (op - 'sealed') || coalesce(kernel.unseal_json(op ->> 'sealed'), '{"name": "[erased]"}')
              ELSE op END
$$;

-- Contract: applies the claim and the resolved operations of a log entry (jsonb in the
-- shape of a kernel.log row) to kernel.claims, kernel.assertions and the graph.
-- Assertion ids derive from the claim id and the op index. Belief follows through the
-- assertion triggers. Does not apply the entry's conflicts (see project_conflicts).
CREATE FUNCTION kernel.project_ops(entry jsonb) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  v_offset bigint := (entry ->> 'log_offset')::bigint;
  v_at timestamptz := (entry ->> 'recorded_at')::timestamptz;
  v_agent text := entry ->> 'agent_id';
  c jsonb := entry -> 'claim';
  v_weight numeric;
  v_asr text;
  op jsonb;
  idx int;
  v_node kernel.nodes;
  v_fields text[];
  v_redacted constant text := '[redacted]';
  -- Entries from before kernel 0.4 carry no origins: each of their sources was its own.
  v_origins text[] := CASE
    WHEN c ? 'origins' THEN ARRAY(SELECT jsonb_array_elements_text(c -> 'origins'))
    WHEN c ->> 'source_id' IS NOT NULL THEN ARRAY['source:' || (c ->> 'source_key')]
    ELSE ARRAY[kernel.normalize_origin(c ->> 'source_key')] END;
  v_text text;
  v_open jsonb;
BEGIN
  INSERT INTO kernel.claims (id, log_offset, text, chunk_id, source_id, source_key, agent_id, basis, modality,
                             polarity, confidence, trust, resolution, unresolved, recorded_at, trace_id, span_id,
                             quote_start, quote_end, run_id, origins, text_key)
  VALUES (c ->> 'id', v_offset, c ->> 'text', c ->> 'chunk_id', c ->> 'source_id', c ->> 'source_key', v_agent,
          c ->> 'basis', c ->> 'modality', (c ->> 'polarity')::smallint, c ->> 'confidence',
          coalesce(c ->> 'trust', entry ->> 'agent_trust'), c ->> 'resolution',
          c -> 'unresolved', v_at, entry ->> 'trace_id', entry ->> 'span_id',
          (c ->> 'quote_start')::int, (c ->> 'quote_end')::int, c ->> 'run', v_origins, c ->> 'text_key');

  v_weight := kernel.level_weight(coalesce(c ->> 'trust', entry ->> 'agent_trust'))
              * kernel.basis_weight(c ->> 'basis') * kernel.level_weight(c ->> 'confidence');
  v_asr := 'asr_' || split_part(c ->> 'id', '_', 2) || '_';

  FOR op, idx IN SELECT o.value, (o.ordinality - 1)::int FROM jsonb_array_elements(entry -> 'ops') WITH ORDINALITY AS o LOOP
    CASE op ->> 'op'

    WHEN 'create' THEN
      v_open := kernel.open_op(op);
      INSERT INTO kernel.nodes (id, type, kind, namespace, name, aliases, identity, props, embedding, trust_level,
                                sealed_key, claim_id, created_offset, updated_offset, created_at, updated_at)
      VALUES (op ->> 'id', op ->> 'type', op ->> 'kind', op ->> 'namespace', v_open ->> 'name',
              ARRAY(SELECT jsonb_array_elements_text(coalesce(v_open -> 'aliases', '[]'))),
              coalesce(v_open -> 'identity', '{}'), coalesce(v_open -> 'props', '{}'),
              CASE WHEN v_open ? 'embedding' THEN (v_open ->> 'embedding')::vector END,
              op ->> 'trust_level', CASE WHEN op ? 'sealed' THEN split_part(op ->> 'sealed', ':', 3) END,
              c ->> 'id', v_offset, v_offset, v_at, v_at);
      INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                     polarity, value, basis, modality, confidence, weight, recorded_at)
      VALUES (v_asr || idx, v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'status', op ->> 'id',
              1, op ->> 'status', c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
      PERFORM kernel.touch(op ->> 'id', v_offset, v_agent);

    WHEN 'assert', 'link', 'unlink' THEN
      IF op ->> 'target' = 'claim' THEN
        INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                       polarity, basis, modality, confidence, weight, recorded_at)
        VALUES (v_asr || idx, v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'claim', op ->> 'claim_node',
                (op ->> 'polarity')::smallint, c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
        PERFORM kernel.touch(op ->> 'claim_node', v_offset, v_agent);
      ELSE
        IF (op ->> 'new_edge')::boolean THEN
          INSERT INTO kernel.edges (id, edge, kind, from_id, to_id, props, claim_id,
                                    created_offset, updated_offset, created_at, updated_at)
          VALUES (op ->> 'edge_id', coalesce(op ->> 'edge', 'same_as'), op ->> 'kind', op ->> 'from', op ->> 'to',
                  coalesce(op -> 'props', '{}'), c ->> 'id', v_offset, v_offset, v_at, v_at);
        END IF;
        INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                       polarity, valid_from, valid_to, basis, modality, confidence, weight, recorded_at)
        VALUES (v_asr || idx, v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'edge', op ->> 'edge_id',
                (op ->> 'polarity')::smallint, (op ->> 'valid_from')::timestamptz, (op ->> 'valid_to')::timestamptz,
                c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
        PERFORM kernel.touch(op ->> 'from', v_offset, v_agent);
        PERFORM kernel.touch(op ->> 'to', v_offset, v_agent);
      END IF;

    WHEN 'promote' THEN
      -- The claim's text in the clear; '[erased]' once a sealed text's key is gone.
      v_text := CASE WHEN c ? 'text_key' THEN coalesce(kernel.unseal(c ->> 'text'), '[erased]') ELSE c ->> 'text' END;
      INSERT INTO kernel.nodes (id, type, kind, namespace, name, props, belief_status,
                                sealed_key, claim_id, created_offset, updated_offset, created_at, updated_at)
      VALUES (op ->> 'node_id', 'Claim', c ->> 'modality', 'core', kernel.claim_node_name(v_text),
              coalesce(op -> 'props', '{}') || jsonb_build_object(
                'text', v_text, 'modality', c ->> 'modality', 'basis', c ->> 'basis',
                'polarity', (c ->> 'polarity')::int, 'confidence', c ->> 'confidence', 'claim_id', c ->> 'id')
              || CASE WHEN jsonb_array_length(coalesce(op -> 'about_edges', '[]')) > 0
                      THEN jsonb_build_object('about_edges', op -> 'about_edges') ELSE '{}' END,
              'unknown', c ->> 'text_key', c ->> 'id', v_offset, v_offset, v_at, v_at);
      INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                     polarity, value, basis, modality, confidence, weight, recorded_at)
      VALUES (v_asr || idx || 's', v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'status', op ->> 'node_id',
              1, 'open', c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
      INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                     polarity, basis, modality, confidence, weight, recorded_at)
      VALUES (v_asr || idx, v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'claim', op ->> 'node_id',
              (c ->> 'polarity')::smallint, c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
      PERFORM kernel.touch(op ->> 'node_id', v_offset, v_agent);

    WHEN 'transition' THEN
      INSERT INTO kernel.assertions (id, log_offset, op_index, claim_id, agent_id, source_key, origins, target_type, target_id,
                                     polarity, value, basis, modality, confidence, weight, recorded_at)
      VALUES (v_asr || idx, v_offset, idx, c ->> 'id', v_agent, c ->> 'source_key', v_origins, 'status', op ->> 'node',
              1, op ->> 'status', c ->> 'basis', c ->> 'modality', c ->> 'confidence', v_weight, v_at);
      PERFORM kernel.touch(op ->> 'node', v_offset, v_agent);

    WHEN 'redact' THEN
      IF op ? 'claim' THEN
        INSERT INTO kernel.claim_redactions (claim_id, log_offset) VALUES (op ->> 'claim', v_offset)
        ON CONFLICT DO NOTHING;
        UPDATE kernel.nodes
        SET name = v_redacted, props = props || jsonb_build_object('text', v_redacted),
            redacted = ARRAY(SELECT DISTINCT unnest(redacted || ARRAY['text']) ORDER BY 1),
            updated_offset = v_offset, updated_at = v_at
        WHERE id = op ->> 'claim' AND type = 'Claim';
      ELSE
        SELECT * INTO v_node FROM kernel.nodes WHERE id = op ->> 'node';
        v_fields := ARRAY(SELECT jsonb_array_elements_text(op -> 'fields'));
        UPDATE kernel.nodes
        SET name = CASE WHEN 'name' = ANY (v_fields) OR ('text' = ANY (v_fields) AND type = 'Claim')
                        THEN v_redacted ELSE name END,
            aliases = CASE WHEN 'aliases' = ANY (v_fields) THEN '{}' ELSE aliases END,
            identity = CASE WHEN 'identity' = ANY (v_fields) THEN '{}' ELSE identity END,
            props = CASE
                      WHEN 'props' = ANY (v_fields) THEN
                        (SELECT coalesce(jsonb_object_agg(k, v_redacted), '{}') FROM jsonb_object_keys(props) AS k)
                      WHEN 'text' = ANY (v_fields) AND type = 'Claim' THEN props || jsonb_build_object('text', v_redacted)
                      ELSE props END,
            embedding = CASE WHEN 'name' = ANY (v_fields) OR 'aliases' = ANY (v_fields) THEN NULL ELSE embedding END,
            redacted = ARRAY(SELECT DISTINCT unnest(redacted || v_fields) ORDER BY 1),
            updated_offset = v_offset, updated_at = v_at
        WHERE id = v_node.id;
      END IF;
      IF op ? 'node' THEN
        PERFORM kernel.touch(op ->> 'node', v_offset, v_agent);
      END IF;

    END CASE;
  END LOOP;
END
$$;

-- Contract: applies the single-valued conflicts recorded in a log entry. The conflict
-- trigger then marks the edges contested while the conflict is active.
CREATE FUNCTION kernel.project_conflicts(entry jsonb) RETURNS void
LANGUAGE sql AS $$
  INSERT INTO kernel.conflicts (edge_a, edge_b, rule, log_offset, recorded_at)
  SELECT x ->> 'edge_a', x ->> 'edge_b', x ->> 'rule',
         (entry ->> 'log_offset')::bigint, (entry ->> 'recorded_at')::timestamptz
  FROM jsonb_array_elements(entry -> 'conflicts') AS x
  ON CONFLICT DO NOTHING
$$;

-- Contract: re-projects the nodes whose personal fields came from the data keys p_keys
-- exactly as projecting the log without those keys would (ADR 0022), after kernel.erase has
-- destroyed them. A human agent's name becomes '[erased]' unless it is redacted, and its
-- aliases, identity, props and embedding go. A Claim node's text and name become '[erased]'
-- unless its text, name or props are redacted. Offsets, statuses, belief, edges and the
-- redacted list stay as they are; the AGE mirror follows through its trigger. Returns the
-- ids of the nodes changed, sorted. Deterministic: no clock, no randomness.
CREATE FUNCTION kernel.erase_nodes(p_keys text[]) RETURNS text[]
LANGUAGE sql AS $$
  WITH changed AS (
    UPDATE kernel.nodes SET
      name = CASE
        WHEN type = 'Claim' AND NOT redacted && ARRAY['name', 'text'] THEN kernel.claim_node_name('[erased]')
        WHEN type <> 'Claim' AND NOT 'name' = ANY (redacted) THEN '[erased]'
        ELSE name END,
      aliases = CASE WHEN type = 'Claim' THEN aliases ELSE '{}' END,
      identity = CASE WHEN type = 'Claim' THEN identity ELSE '{}' END,
      props = CASE
        WHEN type <> 'Claim' THEN '{}'
        WHEN redacted && ARRAY['text', 'props'] THEN props
        ELSE props || '{"text": "[erased]"}' END,
      embedding = CASE WHEN type = 'Claim' THEN embedding END
    WHERE sealed_key = ANY (p_keys)
    RETURNING id
  )
  SELECT coalesce(array_agg(id ORDER BY id), '{}') FROM changed
$$;

-- Contract: rebuilds every projection from kernel.log in offset order. Requires empty
-- projections, as in a fresh database holding a copy of the log, sources, chunks, data keys
-- and erasure ledger: what a destroyed key sealed projects as erased.
-- Used by the replay test; never granted to the writer or reader roles.
CREATE FUNCTION kernel.rebuild() RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  r kernel.log;
  n bigint := 0;
BEGIN
  IF EXISTS (SELECT 1 FROM kernel.claims) OR EXISTS (SELECT 1 FROM kernel.nodes) THEN
    RAISE EXCEPTION 'kernel.rebuild needs empty projections';
  END IF;
  FOR r IN SELECT * FROM kernel.log ORDER BY log_offset LOOP
    PERFORM kernel.project_ops(to_jsonb(r));
    PERFORM kernel.project_conflicts(to_jsonb(r));
    n := n + 1;
  END LOOP;
  RETURN n;
END
$$;

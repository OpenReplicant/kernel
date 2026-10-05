-- 50_belief.sql
-- Belief: a pure, versioned function of the assertions, kept current by triggers.
--
-- belief_v2 (ADR 0021), for one edge, claim node or node status:
--   1. Group assertions by source_key; each source counts once, through its latest
--      assertion (same source, newer supersedes).
--   2. Each counted assertion weighs trust x basis x confidence band. Trust is the claim's
--      (kernel.claims.trust): the writer's, or for a reported claim citing a source with
--      an author, the lower of the writer's and the author's.
--   3. Each origin counts once: an assertion spreads its weight evenly over its origins
--      (who the claim comes from), and each origin adds its largest share to each side.
--      Denials (polarity -1) weigh against. With one origin per source this is belief_v1.
--   4. Status: unknown with no weight; contested when both sides reach the credibility
--      threshold (0.25), when counted sources disagree on the validity window, or when
--      the scores tie; otherwise accepted or rejected by the heavier side.
-- No decay: recorded_at never enters the computation. Different sources never
-- overwrite one another, even with the same origin; their disagreement is contested.
-- belief_v1 (ADR 0007) stays defined for comparison; nothing maintains it.

SET ROLE kernel_owner;

CREATE TYPE kernel.belief AS (
  status          text,
  score           numeric,
  for_weight      numeric,
  against_weight  numeric,
  sources_for     int,
  sources_against int,
  valid_from      timestamptz,
  valid_to        timestamptz,
  window_agreed   boolean,
  status_values   text[],
  origins_for     int,
  origins_against int
);

-- Contract: belief status from the two weights and window agreement, per belief_v1 and
-- belief_v2 (the same rule and thresholds). Pure.
CREATE FUNCTION kernel.belief_status_v1(for_weight numeric, against_weight numeric, window_agreed boolean)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN coalesce(for_weight, 0) = 0 AND coalesce(against_weight, 0) = 0 THEN 'unknown'
    WHEN least(for_weight, against_weight) >= 0.25 THEN 'contested'
    WHEN for_weight > against_weight THEN CASE WHEN window_agreed THEN 'accepted' ELSE 'contested' END
    WHEN against_weight > for_weight THEN 'rejected'
    ELSE 'contested'
  END
$$;

-- Contract: belief_v1 for one target ('edge', 'claim' or 'status' with a node id),
-- computed from assertions with log_offset <= max_offset (all when NULL). Passing an
-- offset answers "what did we believe at that point of the log". Reads only
-- kernel.assertions; deterministic.
CREATE FUNCTION kernel.belief_v1(p_target_type text, p_target_id text, max_offset bigint DEFAULT NULL)
RETURNS kernel.belief LANGUAGE sql STABLE AS $$
  WITH counted AS (
    SELECT DISTINCT ON (a.source_key) a.*
    FROM kernel.assertions a
    WHERE a.target_type = p_target_type AND a.target_id = p_target_id
      AND (max_offset IS NULL OR a.log_offset <= max_offset)
    ORDER BY a.source_key, a.log_offset DESC, a.op_index DESC
  ),
  tally AS (
    SELECT
      coalesce(sum(weight) FILTER (WHERE polarity = 1), 0) AS f,
      coalesce(sum(weight) FILTER (WHERE polarity = -1), 0) AS ag,
      count(*) FILTER (WHERE polarity = 1)::int AS nf,
      count(*) FILTER (WHERE polarity = -1)::int AS na
    FROM counted
  ),
  windowed AS (
    -- The window comes from positive assertions; from denials when there are none.
    SELECT * FROM counted
    WHERE polarity = 1 OR NOT EXISTS (SELECT 1 FROM counted WHERE polarity = 1)
  ),
  win AS (
    SELECT
      CASE WHEN bool_or(valid_from IS NULL) THEN NULL ELSE min(valid_from) END AS vf,
      CASE WHEN bool_or(valid_to IS NULL) THEN NULL ELSE max(valid_to) END AS vt,
      count(DISTINCT coalesce(valid_from::text, '-') || '|' || coalesce(valid_to::text, '+')) <= 1 AS agreed
    FROM windowed
  ),
  vals AS (
    SELECT coalesce(array_agg(DISTINCT value ORDER BY value) FILTER (WHERE polarity = 1 AND value IS NOT NULL), '{}') AS v
    FROM counted
  )
  SELECT ROW(
    kernel.belief_status_v1(t.f, t.ag, w.agreed),
    CASE WHEN t.f + t.ag > 0 THEN round(t.f / (t.f + t.ag), 4) END,
    t.f, t.ag, t.nf, t.na,
    w.vf, w.vt, w.agreed,
    vals.v, t.nf, t.na
  )::kernel.belief
  FROM tally t, win w, vals
$$;

-- Contract: belief_v2 for one target ('edge', 'claim' or 'status' with a node id),
-- computed from assertions with log_offset <= max_offset (all when NULL). Like belief_v1,
-- but each origin counts once: a counted assertion spreads its weight evenly over its
-- origins, and each origin adds its largest share to each side. Sums are rounded to four
-- places. Equal to belief_v1 when every source has one origin of its own; adding a source
-- never lowers either side. Reads only kernel.assertions; deterministic.
CREATE FUNCTION kernel.belief_v2(p_target_type text, p_target_id text, max_offset bigint DEFAULT NULL)
RETURNS kernel.belief LANGUAGE sql STABLE AS $$
  WITH counted AS (
    SELECT DISTINCT ON (a.source_key) a.*
    FROM kernel.assertions a
    WHERE a.target_type = p_target_type AND a.target_id = p_target_id
      AND (max_offset IS NULL OR a.log_offset <= max_offset)
    ORDER BY a.source_key, a.log_offset DESC, a.op_index DESC
  ),
  shares AS (
    SELECT c.polarity, o.origin, max(c.weight / cardinality(c.origins)) AS share
    FROM counted c CROSS JOIN LATERAL unnest(c.origins) AS o(origin)
    GROUP BY c.polarity, o.origin
  ),
  tally AS (
    SELECT
      trim_scale(round(coalesce((SELECT sum(share) FROM shares WHERE polarity = 1), 0), 4)) AS f,
      trim_scale(round(coalesce((SELECT sum(share) FROM shares WHERE polarity = -1), 0), 4)) AS ag,
      (SELECT count(*) FROM counted WHERE polarity = 1)::int AS nf,
      (SELECT count(*) FROM counted WHERE polarity = -1)::int AS na,
      (SELECT count(*) FROM shares WHERE polarity = 1)::int AS ofor,
      (SELECT count(*) FROM shares WHERE polarity = -1)::int AS oag
  ),
  windowed AS (
    -- The window comes from positive assertions; from denials when there are none.
    SELECT * FROM counted
    WHERE polarity = 1 OR NOT EXISTS (SELECT 1 FROM counted WHERE polarity = 1)
  ),
  win AS (
    SELECT
      CASE WHEN bool_or(valid_from IS NULL) THEN NULL ELSE min(valid_from) END AS vf,
      CASE WHEN bool_or(valid_to IS NULL) THEN NULL ELSE max(valid_to) END AS vt,
      count(DISTINCT coalesce(valid_from::text, '-') || '|' || coalesce(valid_to::text, '+')) <= 1 AS agreed
    FROM windowed
  ),
  vals AS (
    SELECT coalesce(array_agg(DISTINCT value ORDER BY value) FILTER (WHERE polarity = 1 AND value IS NOT NULL), '{}') AS v
    FROM counted
  )
  SELECT ROW(
    kernel.belief_status_v1(t.f, t.ag, w.agreed),
    CASE WHEN t.f + t.ag > 0 THEN round(t.f / (t.f + t.ag), 4) END,
    t.f, t.ag, t.nf, t.na,
    w.vf, w.vt, w.agreed,
    vals.v, t.ofor, t.oag
  )::kernel.belief
  FROM tally t, win w, vals
$$;

-- Contract: recomputes contested_with and belief_status of one edge from its stored
-- tallies and window and the stored state of its conflict partners. A conflict is
-- active while neither edge is rejected or unknown and their windows overlap; an
-- accepted edge in an active conflict is contested. Returns true when the row changed.
CREATE FUNCTION kernel.refresh_edge_conflicts(p_edge_id text, p_offset bigint, p_at timestamptz)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  e kernel.edges;
  base text;
  partners text[];
  final text;
BEGIN
  SELECT * INTO e FROM kernel.edges WHERE id = p_edge_id;
  base := kernel.belief_status_v1(e.belief_for, e.belief_against, e.window_agreed);
  SELECT coalesce(array_agg(p.id ORDER BY p.id), '{}') INTO partners
  FROM kernel.conflicts c
  JOIN kernel.edges p ON p.id = CASE WHEN c.edge_a = p_edge_id THEN c.edge_b ELSE c.edge_a END
  WHERE (c.edge_a = p_edge_id OR c.edge_b = p_edge_id)
    AND base NOT IN ('rejected', 'unknown')
    AND kernel.belief_status_v1(p.belief_for, p.belief_against, p.window_agreed) NOT IN ('rejected', 'unknown')
    AND tstzrange(e.valid_from, e.valid_to, '[)') && tstzrange(p.valid_from, p.valid_to, '[)');
  final := CASE WHEN base = 'accepted' AND partners <> '{}' THEN 'contested' ELSE base END;
  IF final IS DISTINCT FROM e.belief_status OR partners IS DISTINCT FROM e.contested_with THEN
    UPDATE kernel.edges
    SET belief_status = final, contested_with = partners, updated_offset = p_offset, updated_at = p_at
    WHERE id = p_edge_id;
    RETURN true;
  END IF;
  RETURN false;
END
$$;

-- Contract: recomputes an edge's tallies, window and status from its assertions, then
-- its conflict state; when its window or base status changed, refreshes its conflict
-- partners too. For supersedes edges, refreshes superseded_by on the target claim.
CREATE FUNCTION kernel.refresh_edge(p_edge_id text, p_offset bigint, p_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  old kernel.edges;
  b kernel.belief;
  partner text;
BEGIN
  SELECT * INTO old FROM kernel.edges WHERE id = p_edge_id;
  b := kernel.belief_v2('edge', p_edge_id);
  UPDATE kernel.edges
  SET belief_for = b.for_weight, belief_against = b.against_weight, belief_score = b.score,
      sources_for = b.sources_for, sources_against = b.sources_against,
      origins_for = b.origins_for, origins_against = b.origins_against,
      valid_from = b.valid_from, valid_to = b.valid_to, window_agreed = b.window_agreed,
      belief_status = kernel.belief_status_v1(b.for_weight, b.against_weight, b.window_agreed),
      updated_offset = p_offset, updated_at = p_at
  WHERE id = p_edge_id;
  PERFORM kernel.refresh_edge_conflicts(p_edge_id, p_offset, p_at);

  IF old.valid_from IS DISTINCT FROM b.valid_from OR old.valid_to IS DISTINCT FROM b.valid_to
     OR kernel.belief_status_v1(old.belief_for, old.belief_against, old.window_agreed)
        IS DISTINCT FROM kernel.belief_status_v1(b.for_weight, b.against_weight, b.window_agreed) THEN
    FOR partner IN
      SELECT CASE WHEN c.edge_a = p_edge_id THEN c.edge_b ELSE c.edge_a END
      FROM kernel.conflicts c WHERE c.edge_a = p_edge_id OR c.edge_b = p_edge_id
      ORDER BY 1
    LOOP
      PERFORM kernel.refresh_edge_conflicts(partner, p_offset, p_at);
    END LOOP;
  END IF;

  IF old.edge = 'supersedes' THEN
    UPDATE kernel.nodes n
    SET superseded_by = s.ids, updated_offset = p_offset, updated_at = p_at
    FROM (
      SELECT coalesce(array_agg(from_id ORDER BY from_id), '{}') AS ids
      FROM kernel.edges WHERE edge = 'supersedes' AND to_id = old.to_id AND belief_status = 'accepted'
    ) s
    WHERE n.id = old.to_id AND n.superseded_by IS DISTINCT FROM s.ids;
  END IF;
END
$$;

-- Contract: recomputes belief of a Claim node from the assertions targeting it.
CREATE FUNCTION kernel.refresh_claim_node(p_node_id text, p_offset bigint, p_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  b kernel.belief := kernel.belief_v2('claim', p_node_id);
BEGIN
  UPDATE kernel.nodes
  SET belief_status = b.status, belief_score = b.score,
      belief_for = b.for_weight, belief_against = b.against_weight,
      updated_offset = p_offset, updated_at = p_at
  WHERE id = p_node_id;
END
$$;

-- Contract: recomputes a node's lifecycle status from status assertions: the value
-- every counted source agrees on, or 'contested' with the options when they differ.
CREATE FUNCTION kernel.refresh_node_status(p_node_id text, p_offset bigint, p_at timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  b kernel.belief := kernel.belief_v2('status', p_node_id);
BEGIN
  UPDATE kernel.nodes
  SET status = CASE cardinality(b.status_values) WHEN 0 THEN NULL WHEN 1 THEN b.status_values[1] ELSE 'contested' END,
      status_options = CASE WHEN cardinality(b.status_values) > 1 THEN b.status_values ELSE '{}' END,
      updated_offset = p_offset, updated_at = p_at
  WHERE id = p_node_id;
END
$$;

-- Contract: belief trigger. After each assertion, refreshes the belief of its target
-- using the entry's offset and recorded_at (never now()).
CREATE FUNCTION kernel.on_assertion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  CASE NEW.target_type
    WHEN 'edge' THEN PERFORM kernel.refresh_edge(NEW.target_id, NEW.log_offset, NEW.recorded_at);
    WHEN 'claim' THEN PERFORM kernel.refresh_claim_node(NEW.target_id, NEW.log_offset, NEW.recorded_at);
    WHEN 'status' THEN PERFORM kernel.refresh_node_status(NEW.target_id, NEW.log_offset, NEW.recorded_at);
  END CASE;
  RETURN NULL;
END
$$;

CREATE TRIGGER assertions_belief AFTER INSERT ON kernel.assertions
  FOR EACH ROW EXECUTE FUNCTION kernel.on_assertion();

-- Contract: contested-status trigger. A newly recorded conflict refreshes both edges.
CREATE FUNCTION kernel.on_conflict() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM kernel.refresh_edge_conflicts(NEW.edge_a, NEW.log_offset, NEW.recorded_at);
  PERFORM kernel.refresh_edge_conflicts(NEW.edge_b, NEW.log_offset, NEW.recorded_at);
  RETURN NULL;
END
$$;

CREATE TRIGGER conflicts_contested AFTER INSERT ON kernel.conflicts
  FOR EACH ROW EXECUTE FUNCTION kernel.on_conflict();

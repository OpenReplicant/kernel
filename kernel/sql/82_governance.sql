-- 82_governance.sql
-- Governance (ADR 0029, building ADR 0019): who may decide on proposals, checked inside
-- kernel.write. A proposal is a Claim node of kind `proposed`; deciding on it is an
-- approved_by or rejected_by edge from it, or a transition of it to approved or rejected.
-- Decisions come only from a signed-in person through kernel.decide; agents never decide.
-- Proposals and hypotheses are data: their claims assert nothing about other nodes.

SET ROLE kernel_owner;

-- Contract: the email of the person the verified token names (PostgREST puts the token's
-- claims in request.jwt.claims), lower case; NULL without one. Reads only the setting.
CREATE FUNCTION kernel.signed_in_email() RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_raw text := nullif(current_setting('request.jwt.claims', true), '');
BEGIN
  IF v_raw IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN nullif(lower(btrim(v_raw::jsonb ->> 'email')), '');
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END
$$;

-- Contract: the human agent whose email identity key is the signed-in person's; NULL when
-- there is no signed-in person or no such agent yet. Reads only.
CREATE FUNCTION kernel.signed_in_agent() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT n.id FROM kernel.nodes n
  WHERE n.type = 'Agent' AND n.kind = 'human' AND lower(n.identity ->> 'email') = kernel.signed_in_email()
  ORDER BY n.created_offset LIMIT 1
$$;

-- Contract: true when the session acts as kernel_approver: the role PostgREST switches to
-- for a verified token, which no gateway login can take. Inside a SECURITY DEFINER function
-- the setting still names the caller's role. Reads only the setting.
CREATE FUNCTION kernel.approver_session() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('role', true), 'none') = 'kernel_approver'
$$;

-- Contract: true when p_node is an instrument (ADR 0029): a normative Claim node whose
-- props.instruments is true (a protecting claim), or a node such a claim is about and whose
-- about edge is not rejected. Reads only.
CREATE FUNCTION kernel.is_instrument(p_node text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM kernel.nodes n
                 WHERE n.id = p_node AND n.type = 'Claim' AND n.kind = 'normative' AND n.props ->> 'instruments' = 'true')
      OR EXISTS (SELECT 1 FROM kernel.edges e JOIN kernel.nodes c ON c.id = e.from_id
                 WHERE e.edge = 'about' AND e.to_id = p_node AND e.belief_status <> 'rejected'
                   AND c.type = 'Claim' AND c.kind = 'normative' AND c.props ->> 'instruments' = 'true')
$$;

-- Contract: the nodes a proposal is about (its about edges that are not rejected), sorted.
-- Reads only.
CREATE FUNCTION kernel.proposal_about(p_proposal text) RETURNS text[]
LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(DISTINCT e.to_id ORDER BY e.to_id), '{}') FROM kernel.edges e
  WHERE e.edge = 'about' AND e.from_id = p_proposal AND e.belief_status <> 'rejected'
$$;

-- Contract: the distinct agents an approved_by edge from p_proposal points at and that are
-- accepted, sorted. Reads only.
CREATE FUNCTION kernel.proposal_approvers(p_proposal text) RETURNS text[]
LANGUAGE sql STABLE AS $$
  SELECT coalesce(array_agg(DISTINCT e.to_id ORDER BY e.to_id), '{}') FROM kernel.edges e
  WHERE e.edge = 'approved_by' AND e.from_id = p_proposal AND e.belief_status = 'accepted'
$$;

-- Contract: the approvals p_proposal needs: core.instruments_need_two's count when it is
-- about an instrument, else 1. Reads only.
CREATE FUNCTION kernel.approvals_needed(p_proposal text) RETURNS int
LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN EXISTS (SELECT 1 FROM unnest(kernel.proposal_about(p_proposal)) a WHERE kernel.is_instrument(a))
              THEN (SELECT (params ->> 'approvals')::int FROM kernel.rules WHERE id = 'core.instruments_need_two')
              ELSE 1 END
$$;

-- Contract: the systems (nodes of the kinds core.approver_outside_system names) that
-- p_node is, or is part_of through accepted part_of edges, up to six hops. Reads only.
CREATE FUNCTION kernel.systems_of(p_node text) RETURNS text[]
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE up(id, depth) AS (
    SELECT p_node, 0
    UNION
    SELECT e.to_id, u.depth + 1 FROM up u JOIN kernel.edges e ON e.from_id = u.id
    WHERE e.edge = 'part_of' AND e.kind IS NULL AND e.belief_status = 'accepted' AND u.depth < 6
  )
  SELECT coalesce(array_agg(DISTINCT n.id ORDER BY n.id), '{}') FROM up JOIN kernel.nodes n ON n.id = up.id
  WHERE n.kind IN (SELECT jsonb_array_elements_text(params -> 'kinds') FROM kernel.rules
                   WHERE id = 'core.approver_outside_system')
$$;

-- Contract: checks a write's resolved operations against the governance rules and rejects
-- (WMK01, problem governance) what breaks one; returns nothing otherwise. Reads only.
--   p_resolved: the resolved ops; p_agent: the writing agent; p_modality: the claim's;
--   p_claim_id: the claim (its Claim node when p_promoted).
-- core.proposals_are_data: a proposed or hypothetical claim only promotes itself and
-- asserts edges to or from its own Claim node. A decision is an approved_by or rejected_by
-- assertion from a proposal, a transition of a proposal to approved, rejected or open, or a
-- promote of a protecting claim (props.instruments). Decisions need core.decided_by_person,
-- core.no_self_approval and core.approver_outside_system; approvals also need
-- core.instruments_alone, and a transition to approved core.instruments_need_two.
-- core.withdraw_own: only a proposal's writer withdraws it.
CREATE FUNCTION kernel.check_governance(p_resolved jsonb, p_agent text, p_modality text, p_claim_id text,
                                        p_promoted boolean) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE
  op jsonb;
  idx int := -1;
  v_from text;
  v_to text;
  v_edge text;
  n kernel.nodes;
  v_agent kernel.nodes;
  v_proposals text[] := '{}';
  v_approving text[] := '{}';
  v_payload_approvers jsonb := '{}';  -- proposal -> [agents approving it in this payload]
  v_approved text[] := '{}';         -- proposals this payload moves to approved
  v_protect boolean := false;
  v_p text;
  v_about text[];
  v_instruments int;
  v_systems text[];
  v_approvers text[];
  v_needed int;
BEGIN
  FOR op IN SELECT x FROM jsonb_array_elements(p_resolved) x LOOP
    idx := idx + 1;
    v_from := NULL;
    v_to := NULL;
    v_edge := NULL;
    IF op ->> 'op' IN ('assert', 'link', 'unlink') AND coalesce(op ->> 'target', 'edge') = 'edge' THEN
      v_edge := coalesce(op ->> 'edge', CASE WHEN op ->> 'op' IN ('link', 'unlink') THEN 'same_as' END);
      v_from := op ->> 'from';
      v_to := op ->> 'to';
      IF v_from IS NULL OR v_edge IS NULL THEN
        SELECT e.edge, e.from_id, e.to_id INTO v_edge, v_from, v_to FROM kernel.edges e WHERE e.id = op ->> 'edge_id';
      END IF;
    END IF;

    -- Proposals and hypotheses are data.
    IF p_modality IN ('proposed', 'hypothetical') AND op ->> 'op' <> 'promote'
       AND NOT (v_edge IS NOT NULL AND p_promoted AND p_claim_id IN (v_from, v_to)) THEN
      PERFORM kernel.reject('governance', 'core.proposals_are_data',
        format('a %s claim states no facts about other nodes: promote it, relate it with edges to or from its own Claim node, and put the change it describes in props.change', p_modality),
        jsonb_build_object('op_index', idx));
    END IF;

    IF v_edge IN ('approved_by', 'rejected_by') THEN
      SELECT * INTO n FROM kernel.nodes WHERE id = v_from;
      IF (n.type = 'Claim' AND n.kind = 'proposed') OR (v_from = p_claim_id AND p_modality = 'proposed') THEN
        v_proposals := v_proposals || v_from;
        IF v_to IS DISTINCT FROM p_agent THEN
          PERFORM kernel.reject('governance', 'core.decided_by_person',
            format('a decision on a proposal points at the person who makes it: %s is not the writing agent', v_to),
            jsonb_build_object('op_index', idx));
        END IF;
        IF v_edge = 'approved_by' AND coalesce((op ->> 'polarity')::int, 1) = 1 THEN
          v_approving := v_approving || v_from;
          v_payload_approvers := jsonb_set(v_payload_approvers, ARRAY[v_from],
            coalesce(v_payload_approvers -> v_from, '[]') || to_jsonb(v_to));
        END IF;
      END IF;
    ELSIF op ->> 'op' = 'transition' THEN
      SELECT * INTO n FROM kernel.nodes WHERE id = op ->> 'node';
      IF n.type = 'Claim' AND n.kind = 'proposed' THEN
        IF op ->> 'status' = 'withdrawn' THEN
          IF (SELECT agent_id FROM kernel.claims WHERE id = n.id) IS DISTINCT FROM p_agent THEN
            PERFORM kernel.reject('governance', 'core.withdraw_own',
              format('only the agent that wrote proposal %s may withdraw it', n.id), jsonb_build_object('op_index', idx));
          END IF;
        ELSE
          v_proposals := v_proposals || n.id;
          IF op ->> 'status' = 'approved' THEN
            v_approved := v_approved || n.id;
            v_approving := v_approving || n.id;
          END IF;
        END IF;
      END IF;
    ELSIF op ->> 'op' = 'promote' AND op -> 'props' ->> 'instruments' = 'true' THEN
      IF p_modality <> 'normative' THEN
        PERFORM kernel.reject('governance', 'core.decided_by_person',
          'a claim protecting instruments is normative', jsonb_build_object('op_index', idx));
      END IF;
      v_protect := true;
    END IF;
  END LOOP;

  IF cardinality(v_proposals) = 0 AND NOT v_protect THEN
    RETURN;
  END IF;

  -- Decided by a signed-in person, as themselves.
  IF NOT kernel.approver_session() THEN
    PERFORM kernel.reject('governance', 'core.decided_by_person',
      'decisions on proposals and instruments are made by a signed-in person through kernel.decide; agents never approve or reject');
  END IF;
  SELECT * INTO v_agent FROM kernel.nodes WHERE id = p_agent;
  IF v_agent.type IS DISTINCT FROM 'Agent' OR v_agent.kind IS DISTINCT FROM 'human'
     OR p_agent IS DISTINCT FROM kernel.signed_in_agent() THEN
    PERFORM kernel.reject('governance', 'core.decided_by_person',
      'the writing agent must be the human agent of the signed-in person');
  END IF;

  FOR v_p IN SELECT DISTINCT x FROM unnest(v_proposals) x ORDER BY 1 LOOP
    IF (SELECT agent_id FROM kernel.claims WHERE id = v_p) = p_agent THEN
      PERFORM kernel.reject('governance', 'core.no_self_approval',
        format('you wrote proposal %s; someone else must decide on it', v_p));
    END IF;
    v_about := kernel.proposal_about(v_p);
    SELECT coalesce(array_agg(DISTINCT s ORDER BY s), '{}') INTO v_systems
    FROM unnest(v_about) a, unnest(kernel.systems_of(a)) s
    WHERE EXISTS (SELECT 1 FROM kernel.edges e
                  WHERE e.edge = 'part_of' AND e.kind IS NULL AND e.from_id = p_agent AND e.to_id = s
                    AND e.belief_status = 'accepted');
    IF cardinality(v_systems) > 0 THEN
      PERFORM kernel.reject('governance', 'core.approver_outside_system',
        format('you are part of %s, which proposal %s changes; someone outside it must decide',
               array_to_string(v_systems, ', '), v_p),
        jsonb_build_object('systems', to_jsonb(v_systems)));
    END IF;
    IF v_p = ANY (v_approving) THEN
      SELECT count(*) FILTER (WHERE kernel.is_instrument(a)) INTO v_instruments FROM unnest(v_about) a;
      IF v_instruments > 0 AND v_instruments < cardinality(v_about) THEN
        PERFORM kernel.reject('governance', 'core.instruments_alone',
          format('proposal %s changes instruments and other things together; it must be split before it can be approved', v_p));
      END IF;
    END IF;
    IF v_p = ANY (v_approved) THEN
      SELECT coalesce(array_agg(DISTINCT x ORDER BY x), '{}') INTO v_approvers FROM (
        SELECT unnest(kernel.proposal_approvers(v_p)) x
        UNION SELECT jsonb_array_elements_text(coalesce(v_payload_approvers -> v_p, '[]'))) a;
      IF NOT p_agent = ANY (v_approvers) THEN
        PERFORM kernel.reject('governance', 'core.decided_by_person',
          format('approve proposal %s yourself (approved_by) in the decision that approves it', v_p));
      END IF;
      v_needed := kernel.approvals_needed(v_p);
      IF cardinality(v_approvers) < v_needed THEN
        PERFORM kernel.reject('governance', 'core.instruments_need_two',
          format('proposal %s is about an instrument: it needs %s approvals from distinct people and has %s',
                 v_p, v_needed, cardinality(v_approvers)));
      END IF;
    END IF;
  END LOOP;
END
$$;

RESET ROLE;

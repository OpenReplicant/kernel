-- 87_decide.sql
-- kernel.decide (ADR 0029): the one way a signed-in person decides on a proposal or
-- protects instruments. It runs only as kernel_approver; the person comes from the verified
-- token, never from an argument; and it writes through kernel.write, whose governance check
-- (82_governance.sql) holds the rules. The log holds ordinary entries.

SET ROLE kernel_owner;

-- Contract: kernel.decide(request) records a decision of the signed-in person.
-- request: {"action": "approve" | "reject", "proposal": <Claim node id>, "reason"?} or
--          {"action": "protect", "about": [node id, ...], "reason"?}
-- - The person is the human agent whose email identity key is the token's email
--   (kernel.signed_in_agent); on their first decision it self-registers, sealed.
-- - approve: approved_by to the person; the proposal moves to approved once it has the
--   approvals it needs (kernel.approvals_needed), else it stays open.
-- - reject: rejected_by to the person and the proposal moves to rejected.
-- - protect: a normative claim about the nodes, props.instruments true: proposals about them
--   then need two people's approval.
-- Rejects (WMK01) a session that is not kernel_approver, a token without an email, a
-- proposal that is not an open Claim node of kind proposed, a second approval by the same
-- person, and whatever kernel.write's rules refuse. Returns kernel.write's result plus
-- {"decision", "agent_id", "approvals", "needed"} (the last two for approvals).
CREATE FUNCTION kernel.decide(p_request jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, kernel, public, pg_temp
AS $$
DECLARE
  v_action text := p_request ->> 'action';
  v_reason text := nullif(btrim(p_request ->> 'reason'), '');
  v_email text := kernel.signed_in_email();
  v_claims jsonb;
  v_agent text;
  v_name text;
  v_proposal kernel.nodes;
  v_text text;
  v_ops jsonb;
  v_modality text := 'descriptive';
  v_approvers text[];
  v_needed int;
  v_about text[];
  v_bad text;
  v_result jsonb;
BEGIN
  IF jsonb_typeof(p_request) IS DISTINCT FROM 'object' THEN
    PERFORM kernel.reject('payload', NULL, 'the request must be a JSON object');
  END IF;
  SELECT string_agg(k, ', ') INTO v_bad FROM jsonb_object_keys(p_request) k
  WHERE k NOT IN ('action', 'proposal', 'about', 'reason');
  IF v_bad IS NOT NULL THEN
    PERFORM kernel.reject('payload', NULL, format('unknown request keys: %s', v_bad));
  END IF;
  IF NOT kernel.approver_session() THEN
    PERFORM kernel.reject('governance', 'core.decided_by_person',
      'kernel.decide runs only for a signed-in person (role kernel_approver); agents never decide');
  END IF;
  IF v_email IS NULL OR v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' THEN
    PERFORM kernel.reject('governance', 'core.decided_by_person', 'the token names no person: it has no email');
  END IF;

  v_agent := kernel.signed_in_agent();
  IF v_agent IS NULL THEN
    v_claims := current_setting('request.jwt.claims', true)::jsonb;
    v_name := coalesce(nullif(btrim(v_claims ->> 'name'), ''), split_part(v_email, '@', 1));
    v_result := kernel.write(jsonb_build_object(
      'claim', jsonb_build_object('text', format('%s signed in to decide on proposals.', v_name),
                                  'basis', 'observed', 'modality', 'descriptive'),
      'read_at_offset', kernel.head_offset(),
      'ops', jsonb_build_array(jsonb_build_object(
        'op', 'create', 'type', 'Agent', 'kind', 'human', 'name', v_name,
        'identity', jsonb_build_object('email', v_email), 'self', true))), NULL);
    v_agent := v_result ->> 'agent_id';
  END IF;

  CASE v_action
  WHEN 'approve', 'reject' THEN
    SELECT * INTO v_proposal FROM kernel.nodes
    WHERE id = p_request ->> 'proposal' AND type = 'Claim' AND kind = 'proposed';
    IF v_proposal.id IS NULL THEN
      PERFORM kernel.reject('reference', NULL,
        format('%s is not a proposal (a Claim node of kind proposed)', coalesce(p_request ->> 'proposal', 'null')),
        jsonb_build_object('field', 'proposal'));
    END IF;
    IF v_proposal.status IS DISTINCT FROM 'open' THEN
      PERFORM kernel.reject('payload', NULL,
        format('proposal %s is %s, not open', v_proposal.id, v_proposal.status), jsonb_build_object('field', 'proposal'));
    END IF;
    SELECT text INTO v_text FROM kernel.claims_view WHERE id = v_proposal.id;
    IF v_text !~ '[.!?]$' THEN
      v_text := v_text || '.';
    END IF;
    IF v_action = 'approve' THEN
      v_approvers := kernel.proposal_approvers(v_proposal.id);
      IF v_agent = ANY (v_approvers) THEN
        PERFORM kernel.reject('payload', NULL, format('you have already approved proposal %s', v_proposal.id));
      END IF;
      v_approvers := v_approvers || v_agent;
      v_needed := kernel.approvals_needed(v_proposal.id);
      v_ops := jsonb_build_array(jsonb_build_object('op', 'assert', 'edge', 'approved_by', 'from', v_proposal.id,
                                                    'to', v_agent));
      IF cardinality(v_approvers) >= v_needed THEN
        v_ops := v_ops || jsonb_build_object('op', 'transition', 'node', v_proposal.id, 'status', 'approved');
      END IF;
      v_text := format('Approved the proposal%s: %s',
        CASE WHEN cardinality(v_approvers) < v_needed
             THEN format(' (approval %s of the %s it needs)', cardinality(v_approvers), v_needed) ELSE '' END, v_text);
    ELSE
      v_ops := jsonb_build_array(
        jsonb_build_object('op', 'assert', 'edge', 'rejected_by', 'from', v_proposal.id, 'to', v_agent),
        jsonb_build_object('op', 'transition', 'node', v_proposal.id, 'status', 'rejected'));
      v_text := format('Rejected the proposal: %s', v_text);
    END IF;
  WHEN 'protect' THEN
    IF jsonb_typeof(p_request -> 'about') IS DISTINCT FROM 'array' OR jsonb_array_length(p_request -> 'about') = 0 THEN
      PERFORM kernel.reject('payload', NULL, 'about must list the node ids to protect', jsonb_build_object('field', 'about'));
    END IF;
    v_about := ARRAY(SELECT DISTINCT x FROM jsonb_array_elements_text(p_request -> 'about') x ORDER BY 1);
    SELECT string_agg(x, ', ') INTO v_bad FROM unnest(v_about) x WHERE NOT EXISTS (SELECT 1 FROM kernel.nodes WHERE id = x);
    IF v_bad IS NOT NULL THEN
      PERFORM kernel.reject('reference', NULL, format('not node ids: %s', v_bad), jsonb_build_object('field', 'about'));
    END IF;
    v_modality := 'normative';
    v_ops := jsonb_build_array(jsonb_build_object('op', 'promote', 'about', to_jsonb(v_about),
                                                  'props', jsonb_build_object('instruments', true)));
    v_text := format('%s %s instruments: a proposal about %s needs the approval of two people.',
      (SELECT string_agg(name, ', ' ORDER BY name) FROM kernel.nodes WHERE id = ANY (v_about)),
      CASE WHEN cardinality(v_about) = 1 THEN 'is one of the' ELSE 'are' END,
      CASE WHEN cardinality(v_about) = 1 THEN 'it' ELSE 'them' END);
  ELSE
    PERFORM kernel.reject('payload', NULL, 'action must be approve, reject or protect', jsonb_build_object('field', 'action'));
  END CASE;

  IF v_reason IS NOT NULL THEN
    v_text := v_text || ' Reason: ' || v_reason;
  END IF;
  v_result := kernel.write(jsonb_build_object(
    'claim', jsonb_build_object('text', v_text, 'basis', 'observed', 'modality', v_modality, 'confidence', 'high'),
    'read_at_offset', kernel.head_offset(),
    'ops', v_ops), v_agent);
  RETURN v_result || jsonb_strip_nulls(jsonb_build_object(
    'decision', v_action, 'agent_id', v_agent,
    'approvals', CASE WHEN v_action = 'approve' THEN cardinality(v_approvers) END,
    'needed', CASE WHEN v_action = 'approve' THEN v_needed END));
END
$$;

RESET ROLE;

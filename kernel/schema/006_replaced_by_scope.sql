-- Kernel v0.4 (D-113; the owner, 2026-11-21: "do what's best", on O-11): set_status checks `replaced_by`.
-- A `set_status` op that names a `replaced_by` must name a claim in the same scope as the claim it re-ranks, or
-- apply raises KK040 ("claim % not found in scope %", as for the claim itself, add_belief and add_verification)
-- and the change set is invalid. Before this, a writer cleared for a secret scope could point an open claim at a
-- secret claim's id, which readers of the open scope then saw in api.claim_status.replaced_by (an id, no content).
-- A `replaced_by` given as the ref of a claim added in the same change set is in the target scope already.
-- Status rows written before this file stay as they are (invariant 2); the notice at the end counts any that
-- point across scopes, so the owner can look. Only the function changes: this is apply_change_set from 004,
-- transient-error handler included, with the one check added.
-- Safe to run again, on a fresh kernel (after 005) or an existing one (kk init applies it to both).
set role kk_owner;
-- `kk init` runs this file as one transaction. Taking the apply lock first (kernel.serialize(), as 005 does) is not
-- needed: `create or replace function` locks no table, so it cannot deadlock with an apply, and an apply that is
-- running keeps the definition it started with. The lock timeout only bounds the catalog lock the replace takes.
set lock_timeout = '10s';

create or replace function kernel.apply_change_set(p_id uuid) returns text
language plpgsql security definer set search_path = kernel, pg_catalog as $$
declare
  cs        kernel.change_set;
  op        jsonb;
  idx       int;
  refs      jsonb := '{}';
  v_claim   uuid;
  v_target  kernel.claim;
  v_subject text; v_predicate text; v_obj_entity text; v_obj_value jsonb; v_quals jsonb;
  v_input   uuid;
  r         jsonb;
  v_art     kernel.artifact;
  v_gen_run kernel.run;
  v_cs_run  kernel.run;
  v_state   text;
begin
  select * into cs from kernel.change_set where id = p_id for update;
  if cs.status not in ('valid','approved') then
    return cs.status;
  end if;
  select * into v_cs_run from kernel.run where id = cs.generated_by;

  begin  -- subtransaction: all ops or none
    for op, idx in select e, o from jsonb_array_elements(cs.ops) with ordinality as t(e, o) loop
      case op->>'op'

      when 'add_entity' then
        insert into kernel.entity(iri) values (kernel.expand(op->>'iri')) on conflict do nothing;

      when 'add_artifact' then
        insert into kernel.artifact(sha256, media_type, byte_size, location, scope, source_uri,
                                    retrieved_at, generated_by, change_set)
        values (op->>'sha256', op->>'media_type', (op->>'byte_size')::bigint, op->>'location',
                cs.target_scope, op->>'source_uri', (op->>'retrieved_at')::timestamptz,
                cs.generated_by, cs.id)
        on conflict (sha256) do nothing;

      when 'add_claim' then
        v_subject    := kernel.expand(op->>'subject');
        v_predicate  := kernel.expand(op->>'predicate');
        v_obj_entity := kernel.expand(op->'object'->>'entity');
        v_obj_value  := op->'object'->'value';
        v_quals      := coalesce(op->'qualifiers', '{}');

        if cs.submitted_by = 'kk_owner' then
          -- vocabulary packs and bootstrap run as migrations (reviewed as code)
          insert into kernel.entity(iri) values (v_predicate) on conflict do nothing;
        elsif not exists (select 1 from kernel.entity where iri = v_predicate) then
          raise exception 'op %: predicate % is not a known term', idx, v_predicate using errcode = 'KK030';
        elsif not kernel.is_approved_property(v_predicate) then
          raise exception 'op %: predicate % is not an approved property', idx, v_predicate using errcode = 'KK031';
        end if;
        insert into kernel.entity(iri) values (v_subject) on conflict do nothing;
        if v_obj_entity is not null then
          insert into kernel.entity(iri) values (v_obj_entity) on conflict do nothing;
        end if;

        if coalesce(jsonb_array_length(op->'references'), 0) = 0
           and coalesce(jsonb_array_length(op->'derived_from'), 0) = 0 then
          raise exception 'op %: a claim needs at least one reference or derived_from (invariant 1)', idx
            using errcode = 'KK032';
        end if;
        if op->>'evidence_type' = 'extracted' and not exists (
             select 1 from jsonb_array_elements(coalesce(op->'references', '[]')) x
             where coalesce(x->>'quote', '') <> '') then
          raise exception 'op %: extracted claims must carry the supporting quote (invariant 1)', idx
            using errcode = 'KK033';
        end if;

        insert into kernel.claim(subject, predicate, object_entity, object_value, qualifiers,
                                 valid_from, valid_to, scope, evidence_type, attributed_to,
                                 generated_by, change_set, statement_key)
        values (v_subject, v_predicate, v_obj_entity, v_obj_value, v_quals,
                (op->>'valid_from')::timestamptz, (op->>'valid_to')::timestamptz,
                cs.target_scope, op->>'evidence_type', cs.attributed_to, cs.generated_by, cs.id,
                kernel.statement_key(v_subject, v_predicate, v_obj_entity, v_obj_value, v_quals))
        returning id into v_claim;

        if op ? 'ref' then
          refs := refs || jsonb_build_object(op->>'ref', v_claim);
        end if;

        for r in select * from jsonb_array_elements(coalesce(op->'references', '[]')) loop
          if r ? 'artifact' then
            select * into v_art from kernel.artifact where sha256 = r->>'artifact';
            if v_art.sha256 is null then
              raise exception 'op %: unknown artifact %', idx, r->>'artifact' using errcode = 'KK034';
            end if;
            if not kernel.readers_subset(cs.target_scope, v_art.scope) then
              raise exception 'op %: artifact % is in a more restrictive scope than %', idx, v_art.sha256, cs.target_scope
                using errcode = 'KK035';
            end if;
          end if;
          insert into kernel.claim_reference(claim_id, artifact, source_uri, quote, quote_prefix,
                                             quote_suffix, span)
          values (v_claim, r->>'artifact', r->>'source_uri', r->>'quote', r->>'quote_prefix',
                  r->>'quote_suffix', r->'span');
        end loop;

        for r in select * from jsonb_array_elements(coalesce(op->'derived_from', '[]')) loop
          v_input := coalesce((refs->>(r#>>'{}'))::uuid, (r#>>'{}')::uuid);
          select * into v_target from kernel.claim where id = v_input;
          if v_target.id is null then
            raise exception 'op %: derived_from % not found', idx, r#>>'{}' using errcode = 'KK036';
          end if;
          if not kernel.readers_subset(cs.target_scope, v_target.scope) then
            raise exception 'op %: input claim % is in a more restrictive scope than % (invariant 3)',
              idx, v_input, cs.target_scope using errcode = 'KK037';
          end if;
          insert into kernel.claim_derivation(claim_id, derived_from) values (v_claim, v_input);
        end loop;

      when 'set_status' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        if op ? 'expect_status' and kernel.current_status(v_claim) <> op->>'expect_status' then
          raise exception 'op %: claim % status is %, expected %', idx, v_claim,
            kernel.current_status(v_claim), op->>'expect_status' using errcode = 'KK409';
        end if;
        -- O-11 (D-113): a replacement is a claim in the claim's own scope, as the claim itself is. The id is
        -- shown to readers of the scope (api.claim_status.replaced_by), so it must not name a claim they cannot read.
        if op->>'replaced_by' is not null and not exists (
             select 1 from kernel.claim
             where id = coalesce((refs->>(op->>'replaced_by'))::uuid, (op->>'replaced_by')::uuid)
               and scope = cs.target_scope) then
          raise exception 'op %: claim % not found in scope %', idx, op->>'replaced_by', cs.target_scope
            using errcode = 'KK040';
        end if;
        if op->>'status' = 'deprecated' and op->>'reason' is null then
          raise exception 'op %: deprecating needs a reason', idx using errcode = 'KK041';
        end if;
        insert into kernel.claim_status(claim_id, status, reason, replaced_by, invalidated_by_run, change_set)
        values (v_claim, op->>'status', op->>'reason',
                coalesce((refs->>(op->>'replaced_by'))::uuid, (op->>'replaced_by')::uuid),
                case when op->>'reason' in ('refuted','retracted') then cs.generated_by end,
                cs.id);

      when 'add_belief' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        insert into kernel.entity(iri) values (kernel.expand(op->>'model')) on conflict do nothing;
        insert into kernel.belief(claim_id, model, value, explanation, computed_by, change_set)
        values (v_claim, kernel.expand(op->>'model'), (op->>'value')::real,
                coalesce(op->'explanation', '{}'), cs.generated_by, cs.id);

      when 'add_verification' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        select * into v_gen_run from kernel.run where id = v_target.generated_by;
        -- invariant 5: no method verifies its own output
        if v_gen_run.agent = v_cs_run.agent
           or (v_gen_run.module is not null and v_gen_run.module = v_cs_run.module)
           or kernel.expand(op->>'method') = v_gen_run.module then
          raise exception 'op %: claim % cannot be verified by the agent or module that produced it (invariant 5)',
            idx, v_claim using errcode = 'KK050';
        end if;
        insert into kernel.entity(iri) values (kernel.expand(op->>'method')) on conflict do nothing;
        insert into kernel.verification(claim_id, verdict, method, run, details, change_set)
        values (v_claim, op->>'verdict', kernel.expand(op->>'method'), cs.generated_by,
                coalesce(op->'details', '{}'), cs.id);

      else
        raise exception 'op %: unknown op %', idx, op->>'op' using errcode = 'KK039';
      end case;
    end loop;

    update kernel.change_set set status = 'applied', applied_at = now() where id = p_id;
    v_state := 'applied';
  exception
    -- O-9 (D-111): a transient failure is not a verdict on the change set. Re-raised, it fails the caller's
    -- statement, the change set stays where it was (submitted or awaiting_review) and is tried again.
    when deadlock_detected or serialization_failure or lock_not_available or query_canceled
         or out_of_memory or insufficient_resources or disk_full or too_many_connections then
      raise;
    when others then
    update kernel.change_set
       set status = case when sqlstate = 'KK409' then 'conflict' else 'invalid' end,
           validation_report = coalesce(validation_report, '{}'::jsonb)
                               || jsonb_build_object('apply_error', sqlerrm, 'sqlstate', sqlstate)
     where id = p_id;
    v_state := case when sqlstate = 'KK409' then 'conflict' else 'invalid' end;
  end;

  perform pg_notify('kernel_change_set',
                    jsonb_build_object('id', p_id, 'status', v_state, 'scope', cs.target_scope)::text);
  return v_state;
end $$;
revoke all on function kernel.apply_change_set(uuid) from public;

-- Report, never repair: status rows that already point at a claim in another scope (or at none).
do $$
declare n bigint;
begin
  select count(*) into n
    from kernel.claim_status s
    join kernel.claim c on c.id = s.claim_id
    left join kernel.claim r on r.id = s.replaced_by
   where s.replaced_by is not null and r.scope is distinct from c.scope;
  raise notice 'D-113: % existing status row(s) point at a replacement in another scope (left as they are)', n;
end $$;

reset lock_timeout;
reset role;

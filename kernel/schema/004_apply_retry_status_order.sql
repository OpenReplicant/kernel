-- Kernel v0.2 (D-111; the owner's answers to O-9 and O-10, 2026-11-21): two fixes to 001 found by D-109's review.
--   O-9   apply re-raises transient errors (deadlock, serialization failure, lock or statement timeout, out of
--         memory or disk) instead of marking the change set invalid for good: the caller's statement fails, the
--         change set keeps its state and is tried again. Data errors are verdicts, as before.
--   O-10  claim_status gets `seq`, an identity in insert order; a claim's current status is its last row by
--         `seq`, not by `recorded_at` (a transaction's start time, which ties within one transaction and puts a
--         later-committing earlier-started write first). Applies are serialized (D-109), so insert order is
--         apply order. Existing rows are numbered by their change set's apply order, then transaction start.
-- Safe to run again, on a fresh kernel (after 003) or an existing one (kk init applies it to both).
set role kk_owner;
-- Adding the column locks claim_status; give up after a while rather than queue every reader behind it.
-- `kk init` can simply be run again.
set lock_timeout = '10s';

-- ---------------------------------------------------------------- O-10: status history in insert order
-- Existing rows are numbered in the order they were written, as far as the record shows it: by the apply order
-- of their change set (kernel.applied, D-109), then by transaction start, then by their place in the table.
-- Not by their place alone: once vacuum lets new rows reuse old space, that is not the order they were written
-- in (found by D-111's review). Then the column becomes an identity continuing above the highest number.
do $$
begin
  if not exists (select 1 from information_schema.columns
                 where table_schema = 'kernel' and table_name = 'claim_status' and column_name = 'seq') then
    alter table kernel.claim_status add column seq bigint;
    perform set_config('kernel.redacting', 'on', true);       -- forbid_mutation lets this one update through
    update kernel.claim_status s set seq = o.n
      from (select st.ctid as t,
                   row_number() over (order by a.seq nulls last, st.recorded_at, st.ctid) as n
              from kernel.claim_status st left join kernel.applied a on a.change_set = st.change_set) o
     where s.ctid = o.t;
    perform set_config('kernel.redacting', 'off', true);
    alter table kernel.claim_status alter column seq set not null;
    alter table kernel.claim_status alter column seq add generated always as identity;
    perform setval(pg_get_serial_sequence('kernel.claim_status', 'seq'),
                   (select coalesce(max(seq), 0) + 1 from kernel.claim_status), false);
  end if;
end $$;
create index if not exists claim_status_claim_seq on kernel.claim_status (claim_id, seq desc);

create or replace function kernel.current_status(p_claim uuid) returns text
language sql stable as $$
  select coalesce((select status from kernel.claim_status
                   where claim_id = p_claim order by seq desc limit 1), 'normal')
$$;

create or replace function api.explain(claim_id uuid) returns jsonb
language sql stable as $$
  select jsonb_build_object(
    'claim',        (select to_jsonb(c) from api.claims c where c.id = claim_id),
    'references',   coalesce((select jsonb_agg(to_jsonb(r) - 'claim_id') from kernel.claim_reference r where r.claim_id = explain.claim_id), '[]'),
    'derived_from', coalesce((select jsonb_agg(d.derived_from) from kernel.claim_derivation d where d.claim_id = explain.claim_id), '[]'),
    'used_by',      coalesce((select jsonb_agg(d.claim_id) from kernel.claim_derivation d where d.derived_from = explain.claim_id), '[]'),
    'status',       coalesce((select jsonb_agg(to_jsonb(s) - 'claim_id' order by s.seq) from kernel.claim_status s where s.claim_id = explain.claim_id), '[]'),
    'beliefs',      coalesce((select jsonb_agg(to_jsonb(b) - 'claim_id' order by b.computed_at) from kernel.belief b where b.claim_id = explain.claim_id), '[]'),
    'verifications',coalesce((select jsonb_agg(to_jsonb(v) - 'claim_id') from kernel.verification v where v.claim_id = explain.claim_id), '[]'),
    'run',          (select to_jsonb(r) from kernel.run r join kernel.claim c on c.generated_by = r.id where c.id = explain.claim_id))
$$;

-- ---------------------------------------------------------------- O-9: transient errors are retried, not verdicts
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

-- Edges written before this file followed the old order; recompute those whose claim has more than one status row.
do $$
declare r record;
begin
  if to_regclass('kernel.current_edge') is not null then
    perform kernel.serialize();                    -- refresh_edge's callers hold the apply lock
    for r in select claim_id from kernel.claim_status group by claim_id having count(*) > 1 loop
      perform kernel.refresh_edge(r.claim_id, (select st.change_set from kernel.claim_status st
                                               where st.claim_id = r.claim_id order by st.seq desc limit 1));
    end loop;
  end if;
end $$;

reset lock_timeout;
reset role;

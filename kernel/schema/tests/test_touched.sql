-- Kernel v0.3 (D-112): api.touched. Run after test_current_edge.sql (it reuses test_kernel.sql's roles, scopes and
-- helpers and test_current_edge.sql's t_cs and t_link). Any failed expectation raises; the last line prints
-- TOUCHED TESTS PASSED.

\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

-- every row of the four tables appears once, with its kind, and nothing else does
select public.check((select count(*) from api.touched) =
  (select count(*) from kernel.claim) + (select count(*) from kernel.claim_status)
  + (select count(*) from kernel.belief) + (select count(*) from kernel.verification),
  'api.touched has one row per claim, status, belief and verification row (as a superuser)');

set role t_extractor;
select api.start_run('ex:module/touched') as trun \gset
select set_config('t.erun', :'trun', false);
select public.t_cs('touch:1', 'urn:kk:scope:default', jsonb_build_array(public.t_link('a', 'ex:t/a', 'ex:t/b'))) as cs_t1 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_t1'::uuid, true) = 'applied', 'a link applies');
reset role;
select id as t1 from kernel.claim where change_set = :'cs_t1'::uuid \gset

set role t_extractor;
select public.check((select array_agg(kind order by kind) from api.touched where change_set = :'cs_t1'::uuid) = array['added'],
                    'an added claim shows as added (a claim has no status row until one is set)');
select public.check((select subject = kernel.expand('ex:t/a') and object_entity = kernel.expand('ex:t/b') and scope = 'urn:kk:scope:default'
                     from api.touched where change_set = :'cs_t1'::uuid and kind = 'added'), 'with its subject, object and scope');
select public.t_cs('touch:2', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'t1', 'status', 'preferred', 'reason', 'review'))) as cs_t2 \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_t2'::uuid, true);
reset role;
set role t_extractor;
select public.check((select array_agg(kind) from api.touched where change_set = :'cs_t2'::uuid) = array['status']
                    and (select claim_id from api.touched where change_set = :'cs_t2'::uuid) = :'t1'::uuid,
                    'a status change shows the claim it re-ranked');
reset role;

-- belief and verification, from another agent (no method verifies its own output)
set role t_verifier;
select api.start_run('ex:module/verifier') as vrun \gset
select (api.submit_change_set(jsonb_build_object(
  'idempotency_key', 'touch:3', 'attributed_to', 'urn:kk:agent:verifier', 'generated_by', :'vrun',
  'target_scope', 'urn:kk:scope:default', 'ops', jsonb_build_array(
    jsonb_build_object('op', 'add_verification', 'claim', :'t1', 'verdict', 'supported', 'method', 'ex:module/verifier'),
    jsonb_build_object('op', 'add_belief', 'claim', :'t1', 'model', 'urn:kk:module:belief-corroboration-v0', 'value', 0.9))))->>'id') as cs_t3 \gset
select set_config('t.cs_t3', :'cs_t3', false);
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_t3'::uuid, true) = 'applied', 'a belief and a verification apply');
reset role;
set role t_extractor;
select public.check((select array_agg(kind order by kind) from api.touched where change_set = :'cs_t3'::uuid) = array['belief','verification'],
                    'a belief and a verification show the claim they scored');
reset role;

-- clearance: a secret claim's rows are seen only by those cleared for its scope, of every kind
set role t_extractor;
select public.t_cs('touch:s', 'urn:kk:scope:secret', jsonb_build_array(public.t_link('s', 'ex:t/s', 'ex:t/hidden'))) as cs_ts \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_ts'::uuid, true) = 'applied', 'a secret link applies');
reset role;
select id as ts from kernel.claim where change_set = :'cs_ts'::uuid \gset
set role t_extractor;
select public.t_cs('touch:s2', 'urn:kk:scope:secret', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'ts', 'status', 'preferred', 'reason', 'review'))) as cs_ts2 \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_ts2'::uuid, true);
reset role;
set role t_verifier;
select public.check(not exists (select 1 from api.touched where claim_id = :'ts'::uuid), 'an uncleared reader sees no row for a secret claim');
select public.check(not exists (select 1 from api.touched where object_entity = kernel.expand('ex:t/hidden')), 'nor its object');
select public.check(not exists (select 1 from api.touched where change_set = :'cs_ts2'::uuid), 'nor the status change made to it');
select public.check(exists (select 1 from api.touched where change_set = :'cs_t1'::uuid), 'it still sees what it is cleared for');
reset role;
set role t_extractor;
select public.check((select count(*) from api.touched where claim_id = :'ts'::uuid) = 2, 'a cleared reader sees both rows');
reset role;

-- nobody writes through it
set role t_extractor;
select public.expect_error($$insert into api.touched (change_set, claim_id, kind, subject, object_entity, scope)
  values (gen_random_uuid(), gen_random_uuid(), 'added', 'x', null, 'urn:kk:scope:default')$$, '55000');   -- not updatable, and no write grant either
select public.expect_error($$delete from api.touched$$, '55000');   -- not updatable, and no write grant either
reset role;

-- a secret change set cannot touch an open claim (KK040), so an open claim never carries a hidden change set
set role t_extractor;
select public.t_cs('touch:x', 'urn:kk:scope:secret', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'t1', 'status', 'deprecated', 'reason', 'retracted'))) as cs_tx \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_tx'::uuid, true) = 'invalid', 'a secret change set re-ranking an open claim is invalid');
reset role;
select public.check(not exists (select 1 from api.touched where change_set = :'cs_tx'::uuid), 'and touched nothing');

-- a redacted claim is still listed (redaction keeps identity), its content as redaction left it
select public.check(exists (select 1 from api.touched t join kernel.claim c on c.id = t.claim_id where c.redacted_at is not null),
                    'a redacted claim is still listed');

-- no one may write through it, by grant as well as by shape
select public.check(not has_table_privilege('kk_agent', 'api.touched', 'insert, update, delete'), 'kk_agent has no write grant on it');

-- the change_set indexes are there, and a lookup by change set, or a join from api.applied, reads them: each of the
-- four branches by its change_set index (with a join inside each branch, the join's key never reached them)
select public.check((select count(*) from pg_indexes where schemaname = 'kernel' and indexname in
  ('claim_change_set', 'claim_status_change_set', 'belief_change_set', 'verification_change_set')) = 4,
  'each of the four tables has its change_set index');
analyze kernel.applied, kernel.claim, kernel.claim_status, kernel.belief, kernel.verification;
set enable_seqscan = off;              -- the tables here are small; a large kernel makes the same choice by cost
set max_parallel_workers_per_gather = 0;
create temp table plan_text (q text, line text);
do $$
declare r record; q text;
begin
  foreach q in array array[
    format('select * from api.touched where change_set = %L::uuid', current_setting('t.cs_t3')),
    format('select t.* from api.applied a join api.touched t on t.change_set = a.change_set where a.seq > %s',
           (select max(seq) - 3 from kernel.applied))] loop
    for r in execute 'explain ' || q loop
      insert into plan_text values (q, r."QUERY PLAN");
    end loop;
  end loop;
end $$;
select public.check((select count(distinct ix) from plan_text,
                       unnest(array['claim_change_set', 'claim_status_change_set', 'belief_change_set', 'verification_change_set']) ix
                     where q like '%where change_set%' and line like '%' || ix || '%') = 4,
                    'a lookup by change set reads all four change_set indexes');
select public.check((select count(*) from plan_text where q like '%api.applied%'
                     and line like '%Index Cond: (change_set = applied.change_set)%') = 4,
                    'and a join from api.applied reaches all four by the joined change set');
reset enable_seqscan;
reset max_parallel_workers_per_gather;

\o
select 'TOUCHED TESTS PASSED' as result;

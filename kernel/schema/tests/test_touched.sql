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

-- the change_set indexes are there and used
select public.check((select count(*) from pg_indexes where schemaname = 'kernel' and indexname in
  ('claim_change_set', 'claim_status_change_set', 'belief_change_set', 'verification_change_set')) = 4,
  'each of the four tables has its change_set index');
set enable_seqscan = off;
create temp table plan_text (line text);
do $$
declare r record;
begin
  for r in execute format('explain select * from api.touched where change_set = %L::uuid', current_setting('t.erun')) loop
    insert into plan_text values (r."QUERY PLAN");
  end loop;
end $$;
select public.check(not exists (select 1 from plan_text where line like '%Seq Scan on claim_status%'
                                   or line like '%Seq Scan on belief%' or line like '%Seq Scan on verification%'),
                    'a lookup by change set reads the indexes, not the whole tables');
reset enable_seqscan;

\o
select 'TOUCHED TESTS PASSED' as result;

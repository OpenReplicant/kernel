-- Kernel v0.4 (D-113): set_status rejects a replaced_by outside the claim's scope. Run after test_touched.sql (it
-- reuses test_kernel.sql's roles, scopes and helpers and test_current_edge.sql's t_cs and t_link). Any failed
-- expectation raises; the last line prints REPLACED BY TESTS PASSED.

\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

-- a secret claim and two open claims (a, and b, its replacement), made by a writer cleared for both scopes
set role t_extractor;
select api.start_run('ex:module/replaced-by') as rrun \gset
select set_config('t.erun', :'rrun', false);
select public.t_cs('rb:secret', 'urn:kk:scope:secret', jsonb_build_array(public.t_link('s', 'ex:rb/s', 'ex:rb/t'))) as cs_s \gset
select public.t_cs('rb:open', 'urn:kk:scope:default', jsonb_build_array(
  public.t_link('a', 'ex:rb/a', 'ex:rb/b'), public.t_link('b', 'ex:rb/c', 'ex:rb/d'))) as cs_o \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_s'::uuid, true) = 'applied', 'a secret claim applies');
select public.check(api.record_validation(:'cs_o'::uuid, true) = 'applied', 'two open claims apply');
reset role;
select id as sec from kernel.claim where change_set = :'cs_s'::uuid \gset
select id as oa from kernel.claim where change_set = :'cs_o'::uuid and subject = kernel.expand('ex:rb/a') \gset
select id as ob from kernel.claim where change_set = :'cs_o'::uuid and subject = kernel.expand('ex:rb/c') \gset

-- a replacement in another scope: invalid, KK040, no status row
set role t_extractor;
select public.t_cs('rb:cross', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'oa', 'status', 'deprecated', 'reason', 'superseded', 'replaced_by', :'sec'))) as cs_x \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_x'::uuid, true);
reset role;
select public.check((select status from kernel.change_set where id = :'cs_x'::uuid) = 'invalid'
                    and (select validation_report->>'sqlstate' from kernel.change_set where id = :'cs_x'::uuid) = 'KK040',
                    'a replaced_by in another scope makes the change set invalid with KK040');
select public.check((select validation_report->>'apply_error' from kernel.change_set where id = :'cs_x'::uuid)
                    like 'op 1: claim % not found in scope urn:kk:scope:default',
                    'with the same message as the claim check');
select public.check((select count(*) from kernel.claim_status where claim_id = :'oa'::uuid) = 0, 'and no status row');

-- a replacement that is no claim at all
set role t_extractor;
select public.t_cs('rb:none', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'oa', 'status', 'deprecated', 'reason', 'superseded',
  'replaced_by', '00000000-0000-0000-0000-000000000001'))) as cs_n \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_n'::uuid, true);
reset role;
select public.check((select validation_report->>'sqlstate' from kernel.change_set where id = :'cs_n'::uuid) = 'KK040',
                    'a replaced_by that is no claim is KK040 too');

-- the same-scope replacement, by id, applies
set role t_extractor;
select public.t_cs('rb:same', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'oa', 'status', 'deprecated', 'reason', 'superseded', 'replaced_by', :'ob'))) as cs_ok \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_ok'::uuid, true) = 'applied', 'a replaced_by in the same scope applies');
reset role;
select public.check((select replaced_by from kernel.claim_status where claim_id = :'oa'::uuid) = :'ob'::uuid, 'and is recorded');

-- a replacement added in the same change set, by ref, applies
set role t_extractor;
select public.t_cs('rb:ref', 'urn:kk:scope:default', jsonb_build_array(
  public.t_link('n', 'ex:rb/e', 'ex:rb/f'),
  jsonb_build_object('op', 'set_status', 'claim', :'ob', 'status', 'deprecated', 'reason', 'superseded', 'replaced_by', 'n'))) as cs_r \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_r'::uuid, true) = 'applied', 'a replaced_by given as a ref in the same change set applies');
reset role;
select public.check((select s.replaced_by = c.id from kernel.claim_status s, kernel.claim c
                     where s.claim_id = :'ob'::uuid and c.change_set = :'cs_r'::uuid), 'and points at the new claim');

-- the secret claim's id is nowhere a reader of the open scope can see it
set role t_reviewer;
select public.check((select count(*) from api.claim_status where replaced_by = :'sec'::uuid) = 0
                    and (select count(*) from api.claims where id = :'sec'::uuid) = 0,
                    'an uncleared reader sees the secret id in no api.claim_status row');
reset role;
select public.check(public.edges_match(), 'the edge table still matches its definition');

\o
select 'REPLACED BY TESTS PASSED' as result;

-- M6-T0 (D-109): kernel.current_edge and kernel.applied. Run after 001, 002, 003 and test_kernel.sql (it reuses
-- that file's roles, scopes and helpers). Any failed expectation raises; the last line prints CURRENT EDGE TESTS PASSED.

\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

-- The edge table must always equal its definition: current (not deprecated), not redacted, entity-valued claims.
create function public.edges_match() returns boolean
language sql as $$
  with want as (
    select c.id, c.subject, c.predicate, c.object_entity as object, c.scope, kernel.current_status(c.id) as status
    from kernel.claim c
    where c.object_entity is not null and c.redacted_at is null and kernel.current_status(c.id) <> 'deprecated'),
  have as (select claim_id as id, subject, predicate, object, scope, status from kernel.current_edge)
  select not exists (select * from want except select * from have)
     and not exists (select * from have except select * from want)
$$;

create function public.t_cs(p_key text, p_scope text, p_ops jsonb) returns uuid
language sql as $$
  select (api.submit_change_set(jsonb_build_object(
    'idempotency_key', p_key, 'attributed_to', 'urn:kk:agent:extractor',
    'generated_by', current_setting('t.erun'), 'target_scope', p_scope, 'ops', p_ops))->>'id')::uuid
$$;

create function public.t_link(p_ref text, p_s text, p_o text) returns jsonb
language sql as $$
  select jsonb_build_object('op', 'add_claim', 'ref', p_ref, 'subject', p_s, 'predicate', 'rdfs:seeAlso',
    'object', jsonb_build_object('entity', p_o), 'evidence_type', 'deterministic',
    'references', jsonb_build_array(jsonb_build_object('source_uri', 'https://example.org/edges')))
$$;

select public.check(public.edges_match(), 'the edge table matches its definition after test_kernel.sql');
select public.check((select count(*) from kernel.applied) = (select count(*) from kernel.change_set where status = 'applied'),
                    'every applied change set has one row in kernel.applied');

set role t_extractor;
select api.start_run('ex:module/edges') as erun \gset
select set_config('t.erun', :'erun', false);
select public.t_cs('edge:1', 'urn:kk:scope:default', jsonb_build_array(public.t_link('a', 'ex:e/a', 'ex:e/b'),
  jsonb_build_object('op', 'add_claim', 'subject', 'ex:e/a', 'predicate', 'rdfs:label',
    'object', jsonb_build_object('value', jsonb_build_object('@value', 'A')), 'evidence_type', 'deterministic',
    'references', jsonb_build_array(jsonb_build_object('source_uri', 'https://example.org/edges'))))) as cs_e1 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_e1'::uuid, true) = 'applied', 'an entity claim and a value claim apply');
reset role;

select id as e1 from kernel.claim where change_set = :'cs_e1'::uuid and object_entity is not null \gset
select public.check((select count(*) from kernel.current_edge where change_set = :'cs_e1'::uuid) = 1,
                    'the entity claim has an edge, the value claim none');
select public.check((select status = 'normal' and subject = kernel.expand('ex:e/a') and object = kernel.expand('ex:e/b')
                     from kernel.current_edge where claim_id = :'e1'::uuid), 'the edge carries subject, object and status');
select public.check((select count(*) = 1 from kernel.applied where change_set = :'cs_e1'::uuid), 'the apply is numbered');

-- rank: preferred shows; superseded, retracted and redacted claims lose their edge
set role t_extractor;
select public.t_cs('edge:2', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'e1', 'status', 'preferred', 'reason', 'review'))) as cs_e2 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_e2'::uuid, true) = 'applied', 'a status change applies');
reset role;
select public.check((select status from kernel.current_edge where claim_id = :'e1'::uuid) = 'preferred', 'preferred reaches the edge');
select public.check((select change_set from kernel.current_edge where claim_id = :'e1'::uuid) = :'cs_e2'::uuid,
                    'the edge names the change set that last changed it');

set role t_extractor;
select public.t_cs('edge:3', 'urn:kk:scope:default', jsonb_build_array(public.t_link('n', 'ex:e/a', 'ex:e/c'),
  jsonb_build_object('op', 'set_status', 'claim', :'e1', 'status', 'deprecated', 'reason', 'superseded',
                     'replaced_by', 'n'))) as cs_e3 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_e3'::uuid, true) = 'applied', 'a supersede applies');
reset role;
select id as e3 from kernel.claim where change_set = :'cs_e3'::uuid \gset
select public.check(not exists (select 1 from kernel.current_edge where claim_id = :'e1'::uuid), 'a superseded claim loses its edge');
select public.check(exists (select 1 from kernel.current_edge where claim_id = :'e3'::uuid), 'its replacement has one');

set role t_extractor;
select public.t_cs('edge:4', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'e1', 'status', 'normal', 'reason', 'review'))) as cs_e4 \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_e4'::uuid, true);
reset role;
select public.check(exists (select 1 from kernel.current_edge where claim_id = :'e1'::uuid) and public.edges_match(),
                    'a claim restored to normal gets its edge back');

set role t_extractor;
select public.t_cs('edge:5', 'urn:kk:scope:default', jsonb_build_array(jsonb_build_object(
  'op', 'set_status', 'claim', :'e1', 'status', 'deprecated', 'reason', 'retracted'))) as cs_e5 \gset
reset role;
set role t_validator;
select api.record_validation(:'cs_e5'::uuid, true);
reset role;
select public.check(not exists (select 1 from kernel.current_edge where claim_id = :'e1'::uuid), 'a retracted claim loses its edge');

set role t_steward;
select api.start_run(null) as srun \gset
select api.redact('claim', :'e3', 'erasure request #2', :'srun'::uuid);
reset role;
select public.check(not exists (select 1 from kernel.current_edge where claim_id = :'e3'::uuid), 'redaction removes the edge at once');

-- a failed apply leaves no edge and no number
set role t_extractor;
select public.t_cs('edge:6', 'urn:kk:scope:default', jsonb_build_array(public.t_link('f', 'ex:e/f', 'ex:e/g'),
  jsonb_build_object('op', 'no_such_op'))) as cs_e6 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_e6'::uuid, true) = 'invalid', 'a change set with a bad op is invalid');
reset role;
select public.check(not exists (select 1 from kernel.current_edge e join kernel.claim c on c.id = e.claim_id
                                where c.subject = kernel.expand('ex:e/f')), 'a failed apply leaves no edge');
select public.check(not exists (select 1 from kernel.applied where change_set = :'cs_e6'::uuid), 'a failed apply is not numbered');

-- clearance: an edge and its apply are seen only by those cleared for the scope
set role t_extractor;
select public.t_cs('edge:7', 'urn:kk:scope:secret', jsonb_build_array(public.t_link('s', 'ex:e/s', 'ex:e/t'))) as cs_e7 \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_e7'::uuid, true) = 'applied', 'a secret edge applies');
reset role;
set role t_verifier;
select public.check(not exists (select 1 from api.edges where subject = kernel.expand('ex:e/s')), 'an uncleared reader sees no secret edge');
select public.check(not exists (select 1 from kernel.current_edge where subject = kernel.expand('ex:e/s')), 'not through the table either');
select public.check(not exists (select 1 from api.applied where change_set = :'cs_e7'::uuid), 'nor that its change set was applied');
select public.check(exists (select 1 from api.applied where change_set = :'cs_e1'::uuid), 'it still sees applies in its scopes');
reset role;
set role t_extractor;
select public.check(exists (select 1 from api.edges where subject = kernel.expand('ex:e/s')), 'a cleared reader sees it');
select public.check(exists (select 1 from api.applied where change_set = :'cs_e7'::uuid), 'and its apply');
reset role;

-- nobody writes them but the kernel
set role t_extractor;
select public.expect_error($$insert into kernel.current_edge values (gen_random_uuid(), 'x', 'y', 'z', '{}', null, null,
  'urn:kk:scope:default', 'normal', gen_random_uuid())$$, '42501');
select public.expect_error($$delete from kernel.current_edge$$, '42501');
select public.expect_error($$insert into kernel.applied (change_set, target_scope, applied_at)
  values (gen_random_uuid(), 'urn:kk:scope:default', now())$$, '42501');
select public.expect_error($$select kernel.refresh_edge(gen_random_uuid(), null)$$, '42501');
reset role;
select public.expect_error($$update kernel.applied set applied_at = now()$$, 'KK001');
select public.expect_error($$delete from kernel.applied$$, 'KK001');

-- the order: numbers rise with each apply, and every applied change set has exactly one
select public.check((select bool_and(a.seq > b.seq) from kernel.applied a, kernel.applied b
                     where a.change_set = :'cs_e7'::uuid and b.change_set = :'cs_e1'::uuid), 'a later apply has a higher number');
select public.check((select count(*) from kernel.applied) = (select count(*) from kernel.change_set where status = 'applied'),
                    'still one number per applied change set');
select public.check(public.edges_match(), 'the edge table matches its definition at the end');

\o
select 'CURRENT EDGE TESTS PASSED' as result;

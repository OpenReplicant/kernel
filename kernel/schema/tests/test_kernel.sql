-- Kernel v0 invariant tests. Run against a fresh database after 001 and 002:
--   psql -v ON_ERROR_STOP=1 -f 001_kernel.sql -f 002_bootstrap.sql -f tests/test_kernel.sql
-- Must be run by a superuser (it creates test roles and switches between them).
-- Any failed expectation raises and stops the script; the last line prints ALL KERNEL TESTS PASSED.

\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

-- ---------------------------------------------------------------- helpers
create function public.expect_error(p_sql text, p_code text) returns void
language plpgsql as $$
declare v_ok boolean := false;
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate = p_code then v_ok := true;
    else raise exception 'expected % but got %: %', p_code, sqlstate, sqlerrm;
    end if;
  end;
  if not v_ok then raise exception 'expected error % but it succeeded: %', p_code, p_sql; end if;
end $$;

create function public.check(p_cond boolean, p_msg text) returns void
language plpgsql as $$
begin
  if p_cond is not true then raise exception 'CHECK FAILED: %', p_msg; end if;
  raise notice 'ok: %', p_msg;
end $$;

-- ---------------------------------------------------------------- fixtures
create role t_extractor nologin;  grant kk_agent to t_extractor;
create role t_verifier nologin;   grant kk_agent to t_verifier;
create role t_validator nologin;  grant kk_validator to t_validator;
create role t_reviewer nologin;   grant kk_reviewer to t_reviewer;
create role t_steward nologin;    grant kk_steward to t_steward;
create role t_secret_readers nologin; grant t_secret_readers to t_extractor;

insert into kernel.prefix values ('ex', 'http://example.org/');
insert into kernel.agent(iri, db_role, kind, label) values
  ('urn:kk:agent:extractor', 't_extractor', 'software', 'Test extractor'),
  ('urn:kk:agent:verifier',  't_verifier',  'software', 'Test verifier'),
  ('urn:kk:agent:validator', 't_validator', 'software', 'Test validator'),
  ('urn:kk:agent:reviewer',  't_reviewer',  'person',   'Test reviewer'),
  ('urn:kk:agent:steward',   't_steward',   'person',   'Test steward');
insert into kernel.scope values ('urn:kk:scope:secret', 'Secret test scope');
insert into kernel.scope_grant values ('urn:kk:scope:secret', 't_secret_readers', true, true);
grant t_secret_readers to t_validator;   -- validators must be cleared for the scopes they validate

-- ---------------------------------------------------------------- 1. a normal extracted claim
set role t_extractor;
select api.start_run('ex:module/extractor') as run1 \gset
select set_config('t.run1', :'run1', false);

select api.submit_change_set(jsonb_build_object(
  'idempotency_key', 'test:cs1',
  'attributed_to', 'urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),
  'target_scope', 'urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object(
     'op', 'add_claim', 'ref', 'c1',
     'subject', 'ex:service/billing', 'predicate', 'rdfs:label',
     'object', jsonb_build_object('value', jsonb_build_object('@value', 'Billing service')),
     'evidence_type', 'extracted',
     'references', jsonb_build_array(jsonb_build_object(
        'source_uri', 'https://example.org/README.md', 'quote', 'The Billing service handles invoices')))))
)->>'id' as cs1 \gset
select set_config('t.cs1', :'cs1', false);

select public.check((select status from api.change_sets where id = current_setting('t.cs1')::uuid) = 'submitted',
                    'change set starts as submitted');
select public.expect_error($$select api.record_validation(current_setting('t.cs1')::uuid, true)$$, '42501');
select public.expect_error($$update kernel.claim set qualifiers = '{}'$$, '42501');
select public.check(
  (api.submit_change_set(jsonb_build_object('idempotency_key','test:cs1','attributed_to','urn:kk:agent:extractor',
     'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
     'ops', '[{"op":"add_entity","iri":"ex:x"}]'::jsonb))->>'id') = current_setting('t.cs1'),
  'resubmitting the same idempotency key returns the same change set');

-- attribution cannot be spoofed
select public.expect_error($$select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:spoof','attributed_to','urn:kk:agent:verifier',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops','[{"op":"add_entity","iri":"ex:y"}]'::jsonb))$$, 'KK010');
reset role;

-- validation by the submitter is refused; by a validator it applies
set role t_validator;
select public.check(api.record_validation(current_setting('t.cs1')::uuid, true, '{"shapes":"ok"}') = 'applied',
                    'validated auto-lane change set is applied');
reset role;

set role t_extractor;
select id as claim1 from api.claims where change_set = current_setting('t.cs1')::uuid \gset
select set_config('t.claim1', :'claim1', false);
select public.check((select count(*) from kernel.claim_reference where claim_id = current_setting('t.claim1')::uuid) = 1,
                    'claim keeps its reference and quote');
select public.check((select attributed_to from api.claims where id = current_setting('t.claim1')::uuid) = 'urn:kk:agent:extractor',
                    'claim is attributed to the submitting agent');
reset role;

-- ---------------------------------------------------------------- 2. invariant 1 violations become invalid
set role t_extractor;
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:noquote','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object('op','add_claim','subject','ex:a','predicate','rdfs:label',
     'object', jsonb_build_object('value', jsonb_build_object('@value','A')),
     'evidence_type','extracted','references', jsonb_build_array(jsonb_build_object('source_uri','https://example.org/a'))))))->>'id' as cs_noquote \gset
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:unapproved','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object('op','add_claim','subject','ex:a','predicate','ex:madeUpProperty',
     'object', jsonb_build_object('entity','ex:b'),
     'evidence_type','deterministic','references', jsonb_build_array(jsonb_build_object('source_uri','https://example.org/a'))))))->>'id' as cs_unapproved \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_noquote'::uuid, true) = 'invalid', 'extracted claim without a quote is rejected');
select public.check((select validation_report->>'sqlstate' from api.change_sets where id = :'cs_noquote'::uuid) = 'KK033', 'reason recorded: missing quote');
select public.check(api.record_validation(:'cs_unapproved'::uuid, true) = 'invalid', 'claim with an unapproved predicate is rejected');
reset role;

-- ---------------------------------------------------------------- 3. vocabulary changes take the review lane
set role t_extractor;
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:newprop','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:vocab','lane','auto',
  'ops', jsonb_build_array(jsonb_build_object('op','add_claim','subject','ex:dependsOn','predicate','rdf:type',
     'object', jsonb_build_object('entity','owl:ObjectProperty'),
     'evidence_type','testimony','references', jsonb_build_array(jsonb_build_object('source_uri','https://example.org/proposal'))))))
  as newprop \gset
select public.check((:'newprop'::jsonb)->>'lane' = 'review', 'typing a new property is forced into the review lane');
select set_config('t.cs_newprop', (:'newprop'::jsonb)->>'id', false);
reset role;
set role t_validator;
select public.check(api.record_validation(current_setting('t.cs_newprop')::uuid, true) = 'awaiting_review', 'valid review-lane change set waits for a reviewer');
reset role;
set role t_extractor;
select public.expect_error($$select api.review(current_setting('t.cs_newprop')::uuid, true)$$, '42501');
reset role;
set role t_reviewer;
select public.check(api.review(current_setting('t.cs_newprop')::uuid, true, 'looks right') = 'applied', 'reviewer approval applies it');
reset role;
select public.check(kernel.is_approved_property('http://example.org/dependsOn'), 'the new property is now approved');

-- ---------------------------------------------------------------- 4. scopes, clearance and derived claims
set role t_extractor;
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:secret','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:secret',
  'ops', jsonb_build_array(jsonb_build_object('op','add_claim','ref','s1','subject','ex:service/billing','predicate','ex:dependsOn',
     'object', jsonb_build_object('entity','ex:service/ledger'),
     'evidence_type','deterministic','references', jsonb_build_array(jsonb_build_object('source_uri','https://example.org/private.yaml'))))))->>'id' as cs_secret \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_secret'::uuid, true) = 'applied', 'secret-scope claim applied');
reset role;
set role t_reviewer;
select public.check((select count(*) from api.change_sets where target_scope = 'urn:kk:scope:secret') = 0,
                    'a reviewer without clearance cannot see secret change sets');
reset role;

set role t_extractor;
select public.check((select count(*) from api.claims where scope = 'urn:kk:scope:secret') = 1, 'cleared agent sees the secret claim');
select id as secret_claim from api.claims where scope = 'urn:kk:scope:secret' \gset
select set_config('t.secret_claim', :'secret_claim', false);
-- derive a public claim from a secret one: must be refused (invariant 3)
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:leak','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object('op','add_claim','subject','ex:service/billing','predicate','rdfs:comment',
     'object', jsonb_build_object('value', jsonb_build_object('@value','depends on the ledger')),
     'evidence_type','inferred','derived_from', jsonb_build_array(current_setting('t.secret_claim'))))))->>'id' as cs_leak \gset
reset role;
set role t_verifier;
select public.check((select count(*) from api.claims where scope = 'urn:kk:scope:secret') = 0, 'uncleared agent cannot see the secret claim');
select public.check((select count(*) from kernel.claim_reference r join kernel.claim c on c.id = r.claim_id where c.scope = 'urn:kk:scope:secret') = 0,
                    'uncleared agent cannot see its references either');
select public.expect_error($$select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:nowrite','attributed_to','urn:kk:agent:verifier',
  'generated_by', (select api.start_run('ex:module/verifier'))::text,'target_scope','urn:kk:scope:secret',
  'ops','[{"op":"add_entity","iri":"ex:z"}]'::jsonb))$$, 'KK012');
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_leak'::uuid, true) = 'invalid', 'deriving into a less restrictive scope is rejected');
select public.check((select validation_report->>'sqlstate' from api.change_sets where id = :'cs_leak'::uuid) = 'KK037', 'reason recorded: scope leak');
reset role;

-- ---------------------------------------------------------------- 5. no method verifies its own output
set role t_extractor;
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:selfverify','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object('op','add_verification','claim', current_setting('t.claim1'),
     'verdict','supported','method','ex:module/extractor'))))->>'id' as cs_self \gset
reset role;
set role t_verifier;
select api.start_run('ex:module/verifier') as run2 \gset
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:verify','attributed_to','urn:kk:agent:verifier',
  'generated_by', :'run2','target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(
     jsonb_build_object('op','add_verification','claim', current_setting('t.claim1'),
                        'verdict','supported','method','ex:module/verifier'),
     jsonb_build_object('op','add_belief','claim', current_setting('t.claim1'),
                        'model','urn:kk:module:belief-corroboration-v0','value',0.75,
                        'explanation', jsonb_build_object('independent_sources',1)))))->>'id' as cs_verify \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_self'::uuid, true) = 'invalid', 'self-verification is rejected');
select public.check(api.record_validation(:'cs_verify'::uuid, true) = 'applied', 'independent verification and belief are applied');
reset role;
set role t_extractor;
select public.check((select belief from api.claims where id = current_setting('t.claim1')::uuid) = 0.75, 'api.claims shows the active model''s belief');
reset role;

-- ---------------------------------------------------------------- 6. optimistic concurrency and supersession
set role t_extractor;
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:stale','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(jsonb_build_object('op','set_status','claim', current_setting('t.claim1'),
     'status','deprecated','reason','superseded','expect_status','preferred'))))->>'id' as cs_stale \gset
select api.submit_change_set(jsonb_build_object(
  'idempotency_key','test:supersede','attributed_to','urn:kk:agent:extractor',
  'generated_by', current_setting('t.run1'),'target_scope','urn:kk:scope:default',
  'ops', jsonb_build_array(
    jsonb_build_object('op','add_claim','ref','new','subject','ex:service/billing','predicate','rdfs:label',
       'object', jsonb_build_object('value', jsonb_build_object('@value','Billing API')),
       'evidence_type','extracted','references', jsonb_build_array(jsonb_build_object(
          'source_uri','https://example.org/README.md','quote','The Billing API handles invoices'))),
    jsonb_build_object('op','set_status','claim', current_setting('t.claim1'),
       'status','deprecated','reason','superseded','replaced_by','new','expect_status','normal'))))->>'id' as cs_supersede \gset
reset role;
set role t_validator;
select public.check(api.record_validation(:'cs_stale'::uuid, true) = 'conflict', 'stale expect_status is a conflict, not an overwrite');
select public.check(api.record_validation(:'cs_supersede'::uuid, true) = 'applied', 'supersession applied');
reset role;
set role t_extractor;
select public.check((select status from api.claims where id = current_setting('t.claim1')::uuid) = 'deprecated', 'old claim deprecated, not deleted');
select public.check((select (api.explain(current_setting('t.claim1')::uuid)->'status'->0->>'replaced_by') is not null), 'explain shows what replaced it');
reset role;

-- ---------------------------------------------------------------- 7. immutability and redaction
select public.expect_error($$delete from kernel.claim$$, 'KK001');
select public.expect_error($$update kernel.claim set qualifiers = '{"x":1}'$$, 'KK001');

set role t_extractor;
select public.expect_error($$select api.redact('claim', current_setting('t.claim1'), 'test', current_setting('t.run1')::uuid)$$, '42501');
reset role;
set role t_steward;
select api.start_run(null) as run3 \gset
select api.redact('claim', current_setting('t.claim1'), 'erasure request #1', :'run3'::uuid);
reset role;
set role t_extractor;
select public.check((select object_value is null and redacted_at is not null from api.claims where id = current_setting('t.claim1')::uuid),
                    'redaction removes the value but keeps the claim');
select public.check((select quote is null from kernel.claim_reference where claim_id = current_setting('t.claim1')::uuid),
                    'redaction removes the quote');
select public.check((select statement_key is not null and attributed_to is not null from api.claims where id = current_setting('t.claim1')::uuid),
                    'lineage and attribution survive redaction');
reset role;

-- ---------------------------------------------------------------- 8. runs
set role t_verifier;
select public.expect_error($$select api.finish_run(current_setting('t.run1')::uuid, 'succeeded')$$, 'KK003');
reset role;
set role t_extractor;
select api.finish_run(current_setting('t.run1')::uuid, 'succeeded', '{"tokens": 1200}');
select public.check((select status = 'succeeded' and ended_at is not null from kernel.run where id = current_setting('t.run1')::uuid), 'agent finishes its own run with cost');
reset role;

\o
select 'ALL KERNEL TESTS PASSED' as result;

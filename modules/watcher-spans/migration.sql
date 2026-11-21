-- Module install: watcher-spans (D-106). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_watcher_spans') then
    create role kkm_watcher_spans nologin;
  end if;
end $$;
grant kk_agent to kkm_watcher_spans;
grant kkm_watcher_spans to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:watcher-spans', 'kkm_watcher_spans', 'software', 'Span watcher')
on conflict (iri) do nothing;

-- The verifier reads quotes (as belief models read sources) and which claims it has verified; belief
-- models read verdicts. security_invoker: a row is visible exactly when its claim is (kernel RLS).
create or replace view api.claim_references with (security_invoker = true) as
select claim_id, artifact, source_uri, quote, span from kernel.claim_reference;
create or replace view api.verifications with (security_invoker = true) as
select claim_id, verdict, method, run, details, recorded_at from kernel.verification;
grant select on api.claim_references, api.verifications to kk_agent;
reset role;

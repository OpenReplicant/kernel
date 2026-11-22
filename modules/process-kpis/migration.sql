-- Module install: process-kpis. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_process_kpis') then
    create role kkm_process_kpis nologin;
  end if;
end $$;
grant kk_agent to kkm_process_kpis;
grant kkm_process_kpis to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:process-kpis', 'kkm_process_kpis', 'software', 'Process KPIs')
on conflict (iri) do nothing;

-- The module finds a log by the artifact its case counts cite (api.claims has no references); same view as
-- belief-corroboration's. security_invoker: a reference is visible exactly when its claim is.
create or replace view api.claim_references with (security_invoker = true) as
select claim_id, artifact, source_uri, quote, span from kernel.claim_reference;
grant select on api.claim_references to kk_agent;
reset role;

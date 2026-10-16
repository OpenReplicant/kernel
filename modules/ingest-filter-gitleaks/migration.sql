-- Module install: ingest-filter-gitleaks. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_ingest_filter') then
    create role kkm_ingest_filter nologin;
  end if;
end $$;
grant kk_agent to kkm_ingest_filter;
grant kkm_ingest_filter to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:ingest-filter', 'kkm_ingest_filter', 'software', 'Ingest filter: secrets (gitleaks)')
on conflict (iri) do nothing;
reset role;

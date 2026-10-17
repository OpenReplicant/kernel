-- Module install: anomaly. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_anomaly') then
    create role kkm_anomaly nologin;
  end if;
end $$;
grant kk_agent to kkm_anomaly;
grant kkm_anomaly to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:anomaly', 'kkm_anomaly', 'software', 'Anomaly processor')
on conflict (iri) do nothing;
reset role;

-- Module install: observer-prometheus. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_observer_prometheus') then
    create role kkm_observer_prometheus nologin;
  end if;
end $$;
grant kk_agent to kkm_observer_prometheus;
grant kkm_observer_prometheus to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:observer-prometheus', 'kkm_observer_prometheus', 'software', 'Metrics observer: Prometheus')
on conflict (iri) do nothing;
reset role;

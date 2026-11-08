-- Module install: map-drift (D-100). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_map_drift') then
    create role kkm_map_drift nologin;
  end if;
end $$;
grant kk_agent to kkm_map_drift;
grant kkm_map_drift to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:map-drift', 'kkm_map_drift', 'software', 'Map drift processor')
on conflict (iri) do nothing;
reset role;

-- Module install: map-gaps. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_map_gaps') then
    create role kkm_map_gaps nologin;
  end if;
end $$;
grant kk_agent to kkm_map_gaps;
grant kkm_map_gaps to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:map-gaps', 'kkm_map_gaps', 'software', 'Map gap processor')
on conflict (iri) do nothing;
reset role;

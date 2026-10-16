-- Module install: ui. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_ui') then
    create role kkm_ui nologin;
  end if;
end $$;
grant kk_agent to kkm_ui;
grant kkm_ui to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:ui', 'kkm_ui', 'software', 'Web UI (Caddy + Alpine.js over PostgREST)')
on conflict (iri) do nothing;
reset role;

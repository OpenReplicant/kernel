-- Module install: conformance. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_conformance') then
    create role kkm_conformance nologin;
  end if;
end $$;
grant kk_agent to kkm_conformance;
grant kkm_conformance to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:conformance', 'kkm_conformance', 'software', 'Conformance processor')
on conflict (iri) do nothing;
reset role;

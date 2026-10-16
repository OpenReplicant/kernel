-- Module install: validator-shacl. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_validator') then
    create role kkm_validator nologin;
  end if;
end $$;
grant kk_validator to kkm_validator;
grant kkm_validator to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:validator', 'kkm_validator', 'software', 'Change-set validator (JSON Schema + SHACL)')
on conflict (iri) do nothing;
reset role;

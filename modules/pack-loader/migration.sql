-- Module install: pack-loader. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_pack_loader') then
    create role kkm_pack_loader nologin;
  end if;
end $$;
grant kk_agent to kkm_pack_loader;
grant kkm_pack_loader to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:pack-loader', 'kkm_pack_loader', 'software', 'Vocabulary pack loader')
on conflict (iri) do nothing;
reset role;

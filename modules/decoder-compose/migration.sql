-- Module install: decoder-compose (D-125). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_decoder_compose') then
    create role kkm_decoder_compose nologin;
  end if;
end $$;
grant kk_agent to kkm_decoder_compose;
grant kkm_decoder_compose to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:decoder-compose', 'kkm_decoder_compose', 'software', 'Compose decoder')
on conflict (iri) do nothing;
reset role;

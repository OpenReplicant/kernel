-- Module install: encoder-interaction. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_encoder_interaction') then
    create role kkm_encoder_interaction nologin;
  end if;
end $$;
grant kk_agent to kkm_encoder_interaction;
grant kkm_encoder_interaction to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:encoder-interaction', 'kkm_encoder_interaction', 'software', 'Interaction encoder')
on conflict (iri) do nothing;
reset role;

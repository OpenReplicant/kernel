-- Module install: encoder-bpmn. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_encoder_bpmn') then
    create role kkm_encoder_bpmn nologin;
  end if;
end $$;
grant kk_agent to kkm_encoder_bpmn;
grant kkm_encoder_bpmn to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:encoder-bpmn', 'kkm_encoder_bpmn', 'software', 'BPMN encoder')
on conflict (iri) do nothing;
reset role;

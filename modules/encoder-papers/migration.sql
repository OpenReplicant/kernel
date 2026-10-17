-- Module install: encoder-papers. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_encoder_papers') then
    create role kkm_encoder_papers nologin;
  end if;
end $$;
grant kk_agent to kkm_encoder_papers;
grant kkm_encoder_papers to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:encoder-papers', 'kkm_encoder_papers', 'software', 'Paper encoder')
on conflict (iri) do nothing;
reset role;

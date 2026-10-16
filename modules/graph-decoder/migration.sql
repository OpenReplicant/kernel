-- Module install: graph-decoder. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_graph_decoder') then
    create role kkm_graph_decoder nologin;
  end if;
end $$;
grant kk_agent to kkm_graph_decoder;
grant kkm_graph_decoder to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:graph-decoder', 'kkm_graph_decoder', 'software', 'Graph decoder (self-model view)')
on conflict (iri) do nothing;
reset role;

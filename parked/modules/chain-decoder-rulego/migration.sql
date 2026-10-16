-- Module install: chain-decoder-rulego. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_decoder_rulego') then
    create role kkm_decoder_rulego nologin;
  end if;
end $$;
grant kk_agent to kkm_decoder_rulego;
grant kkm_decoder_rulego to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:chain-decoder-rulego', 'kkm_decoder_rulego', 'software', 'Chain decoder: chain profile -> RuleGo DSL')
on conflict (iri) do nothing;
reset role;

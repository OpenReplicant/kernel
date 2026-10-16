-- Module install: chain-runner-rulego. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_runner_rulego') then
    create role kkm_runner_rulego nologin;
  end if;
end $$;
grant kk_agent to kkm_runner_rulego;
grant kkm_runner_rulego to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:chain-runner-rulego', 'kkm_runner_rulego', 'software', 'RuleGo-Server chain runner')
on conflict (iri) do nothing;
reset role;

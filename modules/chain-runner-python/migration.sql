-- Module install: chain-runner-python. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_runner_py') then
    create role kkm_runner_py nologin;
  end if;
end $$;
grant kk_agent to kkm_runner_py;
grant kkm_runner_py to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:chain-runner-python', 'kkm_runner_py', 'software', 'Bootstrap chain runner (Python)')
on conflict (iri) do nothing;
reset role;

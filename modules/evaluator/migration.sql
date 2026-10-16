-- Module install: evaluator. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_evaluator') then
    create role kkm_evaluator nologin;
  end if;
end $$;
grant kk_agent to kkm_evaluator;
grant kkm_evaluator to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:evaluator', 'kkm_evaluator', 'software', 'Evaluator')
on conflict (iri) do nothing;
reset role;

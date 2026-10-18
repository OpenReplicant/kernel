-- Module install: learner-process. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_learner_process') then
    create role kkm_learner_process nologin;
  end if;
end $$;
grant kk_agent to kkm_learner_process;
grant kkm_learner_process to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:learner-process', 'kkm_learner_process', 'software', 'Process learner')
on conflict (iri) do nothing;
reset role;

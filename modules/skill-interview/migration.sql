-- Module install: skill-interview's worker (M5-T5, D-117). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_interviewer') then
    create role kkm_interviewer nologin;
  end if;
end $$;
grant kk_agent to kkm_interviewer;
grant kkm_interviewer to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:interviewer', 'kkm_interviewer', 'software', 'Interviewer')
on conflict (iri) do nothing;
reset role;

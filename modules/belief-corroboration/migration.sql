-- Module install: belief-corroboration. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_belief') then
    create role kkm_belief nologin;
  end if;
end $$;
grant kk_agent to kkm_belief;
grant kkm_belief to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:belief-corroboration', 'kkm_belief', 'software', 'Belief model: corroboration counting v0')
on conflict (iri) do nothing;
reset role;

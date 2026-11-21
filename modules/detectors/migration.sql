-- Module install: detectors (D-110). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).
-- A detector's owner is another role (its query runs as that role); this login may switch to the roles a
-- detector names, as kk_authenticator may switch to every module role. Scopes other than default: grant
-- the module's role write there (kernel.scope_grant) for the detectors that write events to them.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_detectors') then
    create role kkm_detectors nologin;
  end if;
end $$;
grant kk_agent to kkm_detectors;
grant kkm_detectors to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:detectors', 'kkm_detectors', 'software', 'Detectors')
on conflict (iri) do nothing;
reset role;

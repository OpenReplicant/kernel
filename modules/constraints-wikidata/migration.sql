-- Module install: constraints-wikidata (D-108). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql). The module reads
-- Substrate's read model (schema `substrate`, view-substrate) and api.verifications at run time; it makes no view
-- of its own except api.verifications when no other module has.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_constraints_wikidata') then
    create role kkm_constraints_wikidata nologin;
  end if;
end $$;
grant kk_agent to kkm_constraints_wikidata;
grant kkm_constraints_wikidata to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:constraints-wikidata', 'kkm_constraints_wikidata', 'software', 'Wikidata property constraints')
on conflict (iri) do nothing;

-- Verdicts are read through api.verifications (the span watcher and view-substrate make it; same definition).
do $$ begin
  if to_regclass('api.verifications') is null then
    execute 'create view api.verifications with (security_invoker = true) as
             select claim_id, verdict, method, run, details, recorded_at from kernel.verification';
    execute 'grant select on api.verifications to kk_agent';
  end if;
end $$;
reset role;

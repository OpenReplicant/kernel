-- Module install: truth-maintenance (D-89, M6-T12). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_truth_maintenance') then
    create role kkm_truth_maintenance nologin;
  end if;
end $$;
grant kk_agent to kkm_truth_maintenance;
grant kkm_truth_maintenance to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:truth-maintenance', 'kkm_truth_maintenance', 'software', 'Truth maintenance')
on conflict (iri) do nothing;

-- Verdicts are read through api.verifications (also created by verifier-quotes; the same definition).
-- security_invoker: a row is visible exactly when its claim is (kernel RLS).
create or replace view api.verifications with (security_invoker = true) as
select claim_id, verdict, method, run, details, recorded_at from kernel.verification;
grant select on api.verifications to kk_agent;
reset role;

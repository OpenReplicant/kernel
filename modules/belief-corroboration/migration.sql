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

-- Belief models judge independence by source, so they read claim references (api.claims has none).
-- security_invoker: a reference is visible exactly when its claim is (kernel RLS).
set role kk_owner;
create or replace view api.claim_references with (security_invoker = true) as
select claim_id, artifact, source_uri, quote, span from kernel.claim_reference;
grant select on api.claim_references to kk_agent;
reset role;

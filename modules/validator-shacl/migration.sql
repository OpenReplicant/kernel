-- Module install: validator-shacl. Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_validator') then
    create role kkm_validator nologin;
  end if;
end $$;
grant kk_validator to kkm_validator;
grant kkm_validator to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:validator', 'kkm_validator', 'software', 'Change-set validator (JSON Schema + SHACL)')
on conflict (iri) do nothing;
reset role;

-- The validator reads submitted change sets with their ops (api.change_sets has no ops).
-- security_invoker: kernel RLS still limits it to scopes the validator may read.
set role kk_owner;
create or replace view api.validation_queue with (security_invoker = true) as
select id, idempotency_key, attributed_to, generated_by, target_scope, lane, ops, submitted_at
from kernel.change_set where status = 'submitted';
grant select on api.validation_queue to kk_validator;
reset role;

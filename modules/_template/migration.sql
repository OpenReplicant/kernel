-- Module migration — runs once at install as kk_owner (after a person approves the install).
-- Allowed: create the module's database role, extensions it needs, views in schema `api`,
-- and vocabulary terms via kernel.install_ops (so terms are attributed and referenced like any claim).
-- Not allowed: writing claims directly, altering kernel tables, granting yourself clearance.

-- 1. The module's identity (role name must match module.yaml agent.db_role)
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_example') then
    create role kkm_example nologin;
  end if;
end $$;
grant kk_agent to kkm_example;
grant kkm_example to kk_authenticator;           -- PostgREST may switch to it with a valid JWT

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:example', 'kkm_example', 'software', 'Example module')
on conflict (iri) do nothing;
-- Clearance (kernel.scope_grant) is NOT granted here: a person grants it when approving the install.

-- 2. Terms this module introduces (installed through the normal apply path)
select kernel.install_ops(
  'module:example:terms:0.1.0', 'urn:kk:scope:vocab',
  '[{"op": "add_claim", "subject": "http://example.org/exampleProperty", "predicate": "rdf:type",
     "object": {"entity": "owl:DatatypeProperty"}, "evidence_type": "deterministic",
     "references": [{"source_uri": "file:modules/_template/migration.sql"}]}]'::jsonb,
  'urn:kk:agent:example', 'urn:kk:module:example');
reset role;

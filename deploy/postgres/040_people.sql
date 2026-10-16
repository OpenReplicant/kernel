-- Runs once on first database start: the local owner, a person who reviews and approves changes
-- in the review lane (modules, chains, vocabulary) and may redact. Sign-in for people is static JWTs
-- until an identity provider is chosen (docs/decisions.md O-6).
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'person_owner') then
    create role person_owner nologin;
  end if;
end $$;
grant kk_reviewer, kk_steward to person_owner;
grant person_owner to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:person:owner', 'person_owner', 'person', 'Local owner')
on conflict (iri) do nothing;
reset role;

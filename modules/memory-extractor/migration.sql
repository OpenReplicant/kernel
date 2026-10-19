-- Module install: memory-extractor (M6-T4). Runs once as a superuser; see modules/_template/migration.sql.
-- Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql). The schema
-- `memory_extractor` holds which messages were read (derived state, never a kernel table), so a message is
-- sent to the model once.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_memory_extractor') then
    create role kkm_memory_extractor nologin;
  end if;
end $$;
grant kk_agent to kkm_memory_extractor;
grant kkm_memory_extractor to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:memory-extractor', 'kkm_memory_extractor', 'software', 'Memory extractor')
on conflict (iri) do nothing;
reset role;

create schema if not exists memory_extractor authorization kkm_memory_extractor;
set role kkm_memory_extractor;
create table if not exists memory_extractor.seen (
  claim_id    uuid primary key,          -- a message's as:content claim
  memories    int not null,              -- how many memories it gave
  model       text not null,
  recorded_at timestamptz not null default now()
);
reset role;

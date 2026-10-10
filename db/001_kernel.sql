-- Kernel schema. Domain-free: no table here mentions papers, agents or slots.
-- Plain Postgres 16. A graph projection (AGE) and vectors (pgvector) are later,
-- derived views over kb.assertion; nothing below changes when they arrive.

create schema if not exists kb;

-- Immutable source files, stored by content hash under $PC_DATA (docs/FILESYSTEM.md)
create table kb.source (
  sha256       text primary key,
  path         text not null,                 -- relative to $PC_DATA
  uri          text,
  media_type   text not null,
  license      text,
  retrieved_at timestamptz not null default now()
);

-- Addressable pieces of a source
create table kb.span (
  id        uuid primary key,
  source    text not null references kb.source,
  locator   jsonb not null,                   -- {page, char_start, char_end} | {path, line_start, line_end} | {json_path}
  excerpt   text                              -- short quote, for review
);

-- Anything with identity, including types, roles, predicates and contexts
create table kb.node (
  id     uuid primary key,
  kind   text not null check (kind in
           ('thing','type','role','port','capability','predicate','context','agent','constraint')),
  iri    text unique,
  label  text,
  props  jsonb not null default '{}'          -- contexts: {ctx_kind, closure, parent, conditions}
);

-- Loaded vocabularies (the files themselves live in git under vocab/)
create table kb.vocabulary (
  name      text not null,
  version   text not null,
  sha256    text not null,
  body      jsonb not null,
  loaded_at timestamptz not null default now(),
  primary key (name, version)
);

-- The atom: one fact plus its provenance and times. Never updated in place except
-- status/retracted_at; corrections are new rows linked by 'supersedes'.
create table kb.assertion (
  id           uuid primary key,
  subject      uuid not null references kb.node,
  predicate    uuid not null references kb.node,     -- a node of kind 'predicate'
  object       uuid references kb.node,
  value        jsonb,                                 -- literal object
  context      uuid not null references kb.node,     -- a node of kind 'context'
  method       text not null check (method in
                 ('stated','repo','inferred','defaulted','observed','computed')),
  confidence   real not null check (confidence between 0 and 1),
  status       text not null default 'accepted' check (status in
                 ('staged','accepted','disputed','retracted')),
  valid_from   timestamptz,                           -- null = unknown / always
  valid_to     timestamptz,
  recorded_at  timestamptz not null default now(),
  retracted_at timestamptz,
  asserted_by  uuid not null references kb.node,     -- a node of kind 'agent'
  run_id       uuid,                                  -- set for 'observed'
  check (object is not null or value is not null)
);
create index on kb.assertion (subject, predicate);
create index on kb.assertion (object, predicate);
create index on kb.assertion (context, status);
create index on kb.assertion (method);

create table kb.assertion_arg (
  assertion uuid not null references kb.assertion,
  name      text not null,
  node      uuid references kb.node,
  value     jsonb,
  primary key (assertion, name),
  check (node is not null or value is not null)
);

create table kb.evidence (
  assertion uuid not null references kb.assertion,
  span      uuid not null references kb.span,
  weight    real not null default 1,
  primary key (assertion, span)
);

create table kb.assertion_link (
  from_id uuid not null references kb.assertion,
  to_id   uuid not null references kb.assertion,
  kind    text not null check (kind in ('supersedes','contradicts','corroborates','derived_from')),
  primary key (from_id, to_id, kind)
);

-- Rule from docs/KERNEL.md: 'stated' and 'repo' need evidence. Enforced in the library
-- at write time (evidence rows are inserted in the same transaction) and checked here
-- by a deferred constraint trigger so the rule can't be bypassed.
-- Staged assertions may wait for evidence (e.g. a spec imported before its PDF is stored);
-- the rule applies the moment an assertion is accepted, whether inserted or promoted.
create function kb.check_evidence() returns trigger language plpgsql as $$
begin
  if new.method in ('stated','repo')
     and new.status = 'accepted'
     and not exists (select 1 from kb.evidence e where e.assertion = new.id) then
    raise exception 'assertion % with method % has no evidence span', new.id, new.method;
  end if;
  return null;
end $$;

create constraint trigger assertion_needs_evidence
  after insert or update of status on kb.assertion
  deferrable initially deferred
  for each row execute function kb.check_evidence();

-- Kernel schema. Domain-free: no table here mentions papers, agents' designs or slots.
-- Plain Postgres 16. A graph projection (AGE) and vectors (pgvector) are later,
-- derived views over kb.assertion; nothing below changes when they arrive.
--
-- The database enforces the rules that keep the evidence trail honest
-- (docs/KERNEL.md, "Rules the database enforces"); the library adds vocabulary checks.

create schema if not exists kb;

-- Anything with identity, including types, roles, predicates, contexts, agents and sources
create table kb.node (
  id     uuid primary key,
  kind   text not null check (kind in
           ('thing','type','role','port','capability','predicate','context','agent','source','constraint')),
  iri    text unique,
  label  text,
  props  jsonb not null default '{}'          -- contexts: {ctx_kind, closure, parent, conditions}
);

-- Immutable recordings, stored by content hash under $PC_DATA (docs/FILESYSTEM.md):
-- documents, code snapshots, transcripts, sensor logs, run traces. Each is also a node
-- so the kernel can hold assertions about it (who produced it, how reliable it is).
create table kb.source (
  sha256       text primary key,
  node         uuid not null unique references kb.node,
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
  locator   jsonb not null,                   -- {page, char_start, char_end} | {path, line_start, line_end} | {json_path} | {t_start, t_end}
  excerpt   text,                             -- short quote, for review
  unique (source, locator)
);

-- Loaded vocabularies (the files themselves live in git under vocab/).
-- Reloading a (name, version) with a different sha256 is an error: bump the version.
create table kb.vocabulary (
  name      text not null,
  version   text not null,
  sha256    text not null,
  body      jsonb not null,
  loaded_at timestamptz not null default now(),
  primary key (name, version)
);

-- The atom: one fact plus its provenance and times. Only `status` ever changes,
-- along the lifecycle below; every change is logged in kb.status_change.
create table kb.assertion (
  id           uuid primary key,
  subject      uuid not null references kb.node,
  predicate    uuid not null references kb.node,     -- kind 'predicate'
  object       uuid references kb.node,
  value        jsonb,                                 -- literal object
  context      uuid not null references kb.node,     -- kind 'context'
  method       text not null check (method in
                 ('stated','observed','inferred','computed','defaulted')),
  confidence   real not null check (confidence between 0 and 1),
  status       text not null default 'accepted' check (status in
                 ('staged','accepted','disputed','superseded','retracted')),
  valid_from   timestamptz,                           -- null = unknown / always
  valid_to     timestamptz,
  recorded_at  timestamptz not null default now(),
  asserted_by  uuid not null references kb.node,     -- kind 'agent': who wrote this row
  check ((object is null) <> (value is null))         -- exactly one of object, value
);
create index on kb.assertion (subject, predicate);
create index on kb.assertion (object, predicate);
create index on kb.assertion (context, status);
create index on kb.assertion (method);

-- Extra named participants for n-ary facts
create table kb.assertion_arg (
  assertion uuid not null references kb.assertion,
  name      text not null,
  node      uuid references kb.node,
  value     jsonb,
  primary key (assertion, name),
  check ((node is null) <> (value is null))
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

-- Append-only history of status, so "what did the kernel believe at time T" can be
-- answered exactly. Written by trigger; `by` comes from `set local kb.actor = '<agent uuid>'`.
create table kb.status_change (
  id        bigserial primary key,
  assertion uuid not null references kb.assertion,
  status    text not null,
  at        timestamptz not null default now(),
  by        uuid references kb.node,
  reason    text
);
create index on kb.status_change (assertion, at);


-- ---------------------------------------------------------------------------
-- Rules the database enforces
-- ---------------------------------------------------------------------------

-- 1. Node kinds: the predicate is a predicate, the context a context, the author an agent.
create function kb.check_kinds() returns trigger language plpgsql as $$
begin
  if (select kind from kb.node where id = new.predicate) <> 'predicate' then
    raise exception 'assertion %: predicate node is not of kind predicate', new.id;
  end if;
  if (select kind from kb.node where id = new.context) <> 'context' then
    raise exception 'assertion %: context node is not of kind context', new.id;
  end if;
  if (select kind from kb.node where id = new.asserted_by) <> 'agent' then
    raise exception 'assertion %: asserted_by node is not of kind agent', new.id;
  end if;
  return new;
end $$;

create trigger assertion_kinds before insert on kb.assertion
  for each row execute function kb.check_kinds();

-- 2. Assertions are never edited: only status changes, along the lifecycle
--      staged    -> accepted | retracted
--      accepted  -> disputed | superseded | retracted
--      disputed  -> accepted | superseded | retracted
--    superseded and retracted are final. Nothing in the evidence trail is deleted.
create function kb.guard_assertion() returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'assertions are never deleted; retract instead';
  end if;
  if (new.id, new.subject, new.predicate, new.object, new.value, new.context, new.method,
      new.confidence, new.valid_from, new.valid_to, new.recorded_at, new.asserted_by)
     is distinct from
     (old.id, old.subject, old.predicate, old.object, old.value, old.context, old.method,
      old.confidence, old.valid_from, old.valid_to, old.recorded_at, old.asserted_by) then
    raise exception 'assertion %: only status may change; supersede instead', old.id;
  end if;
  if new.status <> old.status and not (
       (old.status = 'staged'   and new.status in ('accepted','retracted')) or
       (old.status = 'accepted' and new.status in ('disputed','superseded','retracted')) or
       (old.status = 'disputed' and new.status in ('accepted','superseded','retracted'))) then
    raise exception 'assertion %: status % -> % not allowed', old.id, old.status, new.status;
  end if;
  return new;
end $$;

create trigger assertion_immutable before update or delete on kb.assertion
  for each row execute function kb.guard_assertion();

create function kb.append_only() returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;

create trigger source_append_only   before update or delete on kb.source        for each row execute function kb.append_only();
create trigger span_append_only     before update or delete on kb.span          for each row execute function kb.append_only();
create trigger arg_append_only      before update or delete on kb.assertion_arg for each row execute function kb.append_only();
create trigger evidence_append_only before update or delete on kb.evidence      for each row execute function kb.append_only();
create trigger link_append_only     before update or delete on kb.assertion_link for each row execute function kb.append_only();
create trigger status_append_only   before update or delete on kb.status_change for each row execute function kb.append_only();

-- 3. Status history is logged automatically.
create function kb.log_status() returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' or new.status <> old.status then
    insert into kb.status_change (assertion, status, by, reason)
    values (new.id, new.status,
            nullif(current_setting('kb.actor', true), '')::uuid,
            nullif(current_setting('kb.reason', true), ''));
  end if;
  return null;
end $$;

create trigger assertion_status_log after insert or update of status on kb.assertion
  for each row execute function kb.log_status();

-- 4. Evidence: 'stated' and 'observed' facts must point at a recording. Staged assertions
--    may wait for their spans (e.g. a spec imported before its PDF is stored); the rule
--    applies once an assertion is accepted or disputed, whether inserted or promoted.
--    Deferred, so evidence rows can be inserted later in the same transaction.
create function kb.check_evidence() returns trigger language plpgsql as $$
begin
  if new.method in ('stated','observed')
     and new.status in ('accepted','disputed')
     and not exists (select 1 from kb.evidence e where e.assertion = new.id) then
    raise exception 'assertion % with method % has no evidence span', new.id, new.method;
  end if;
  return null;
end $$;

create constraint trigger assertion_needs_evidence
  after insert or update of status on kb.assertion
  deferrable initially deferred
  for each row execute function kb.check_evidence();

-- Knowledge Kernel v0 — core schema
-- Target: PostgreSQL 16+ with no extensions. Extensions (pgvector, pg_cron, pgmq, ...)
-- belong to modules, never to the kernel.
--
-- Five primitives: entity, claim, artifact, run, change set.
-- Everything else (terms, mappings, modules, slots, profiles) is entities + claims.
--
-- Security model
--   * Agents (software or people) act as database roles; PostgREST switches to the role in
--     the caller's JWT. Each agent row maps an agent IRI to exactly one role.
--   * Agents never get INSERT/UPDATE/DELETE on claim tables. The only writes they can make:
--       - insert a run, finish their own run
--       - insert a change set
--     Validators, reviewers and stewards get narrow column-level UPDATE/INSERT rights.
--   * Change sets are applied by kernel.apply_change_set(), owned by kk_owner and callable
--     only from a SECURITY DEFINER trigger, so nothing outside the kernel can apply one.
--   * Row-level security filters every read by scope.
--
-- Run as a superuser (or the database owner) once; objects end up owned by kk_owner.

-- ---------------------------------------------------------------- roles
do $$
declare r text;
begin
  foreach r in array array['kk_owner','kk_agent','kk_validator','kk_reviewer','kk_steward','kk_anon'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
  -- PostgREST connects as kk_authenticator and switches to the JWT role.
  -- Set its password at deploy time: ALTER ROLE kk_authenticator PASSWORD '...';
  if not exists (select 1 from pg_roles where rolname = 'kk_authenticator') then
    create role kk_authenticator login noinherit;
  end if;
end $$;

grant kk_anon to kk_authenticator;
-- Validators, reviewers and stewards are also agents.
grant kk_agent to kk_validator, kk_reviewer, kk_steward;

do $$ begin
  execute format('grant create on database %I to kk_owner', current_database());
end $$;

set role kk_owner;

create schema kernel;
create schema api;

-- ---------------------------------------------------------------- reference tables
create table kernel.prefix (
  prefix text primary key check (prefix ~ '^[A-Za-z][A-Za-z0-9_-]*$'),
  namespace text not null
);

create table kernel.setting (
  key   text primary key,
  value jsonb not null
);

create table kernel.agent (
  iri        text primary key,
  db_role    name unique,                  -- null for agents that never log in (e.g. an external source)
  kind       text not null check (kind in ('person','software','organization')),
  label      text not null,
  created_at timestamptz not null default now()
);

create table kernel.scope (
  iri        text primary key,
  label      text not null,
  created_at timestamptz not null default now()
);

-- Clearance: which roles may read / write which scope. Granted by people, never by models.
create table kernel.scope_grant (
  scope     text not null references kernel.scope,
  db_role   name not null,
  can_read  boolean not null default true,
  can_write boolean not null default false,
  primary key (scope, db_role)
);

-- ---------------------------------------------------------------- primitives
create table kernel.entity (
  iri        text primary key,
  created_at timestamptz not null default now()
);

create table kernel.run (                     -- prov:Activity
  id         uuid primary key default gen_random_uuid(),
  agent      text not null references kernel.agent,          -- prov:wasAssociatedWith
  module     text references kernel.entity,                  -- the module that ran (null: a person acting directly)
  plan_step  text references kernel.entity,                  -- p-plan:correspondsToStep
  started_at timestamptz not null default now(),
  ended_at   timestamptz,
  status     text not null default 'running'
             check (status in ('running','succeeded','failed','cancelled')),
  used       jsonb not null default '[]',                    -- prov:used (artifact hashes, claim ids, entity IRIs)
  cost       jsonb not null default '{}',                    -- tokens, money, cpu seconds
  info       jsonb not null default '{}'
);

create table kernel.change_set (
  id               uuid primary key default gen_random_uuid(),
  idempotency_key  text not null unique,
  submitted_by     name not null default current_user,
  attributed_to    text not null references kernel.agent,    -- prov:wasAttributedTo
  generated_by     uuid not null references kernel.run,      -- prov:wasGeneratedBy
  target_scope     text not null references kernel.scope,
  lane             text not null check (lane in ('auto','review')),
  ops              jsonb not null check (jsonb_typeof(ops) = 'array' and jsonb_array_length(ops) > 0),
  status           text not null default 'submitted'
                   check (status in ('submitted','valid','invalid','awaiting_review',
                                     'approved','rejected','applied','conflict')),
  validated_by     name,
  validation_report jsonb,
  decided_by       name,
  decided_at       timestamptz,
  review_note      text,
  submitted_at     timestamptz not null default now(),
  applied_at       timestamptz
);

create table kernel.artifact (                -- prov:Entity with content; immutable, content-addressed
  sha256        text primary key check (sha256 ~ '^[0-9a-f]{64}$'),
  media_type    text not null,
  byte_size     bigint not null check (byte_size >= 0),
  location      text not null,                -- artifact-store URI
  scope         text not null references kernel.scope,
  source_uri    text,                         -- dcterms:source
  retrieved_at  timestamptz,
  generated_by  uuid not null references kernel.run,
  change_set    uuid not null references kernel.change_set,
  recorded_at   timestamptz not null default now(),
  redacted_at   timestamptz
);

create table kernel.claim (
  id            uuid primary key default gen_random_uuid(),
  subject       text not null references kernel.entity,
  predicate     text not null references kernel.entity,
  object_entity text references kernel.entity,
  object_value  jsonb,                        -- JSON-LD value object: {"@value":..,"@type":..,"@language":..}
  qualifiers    jsonb not null default '{}',  -- conditions; keys are property IRIs
  valid_from    timestamptz,                  -- valid time (when it is true in the world)
  valid_to      timestamptz,
  scope         text not null references kernel.scope,
  evidence_type text not null
                check (evidence_type in ('deterministic','extracted','inferred','observed','testimony')),
  attributed_to text not null references kernel.agent,
  generated_by  uuid not null references kernel.run,
  change_set    uuid not null references kernel.change_set,
  statement_key text not null,                -- hash of (subject, predicate, object, qualifiers): same statement, any source
  recorded_at   timestamptz not null default now(),   -- recorded time (when we learned it)
  redacted_at   timestamptz,
  check ((object_entity is null) <> (object_value is null) or redacted_at is not null),
  check (valid_to is null or valid_from is null or valid_to > valid_from)
);
create index on kernel.claim (subject, predicate);
create index on kernel.claim (object_entity) where object_entity is not null;
create index on kernel.claim (statement_key);
create index on kernel.claim (scope);

create table kernel.claim_reference (         -- source of a claim; quote/span follow Web Annotation selectors
  claim_id      uuid not null references kernel.claim,
  artifact      text references kernel.artifact,
  source_uri    text,
  quote         text,                         -- oa:TextQuoteSelector exact
  quote_prefix  text,
  quote_suffix  text,
  span          jsonb,                        -- oa:TextPositionSelector {"start":..,"end":..} or another selector
  check (artifact is not null or source_uri is not null)
);
create index on kernel.claim_reference (claim_id);

create table kernel.claim_derivation (        -- prov:wasDerivedFrom between claims
  claim_id     uuid not null references kernel.claim,
  derived_from uuid not null references kernel.claim,
  primary key (claim_id, derived_from)
);
create index on kernel.claim_derivation (derived_from);

create table kernel.claim_status (            -- append-only rank history; latest row wins
  claim_id           uuid not null references kernel.claim,
  status             text not null check (status in ('normal','preferred','deprecated')),
  reason             text check (reason in ('superseded','refuted','retracted','review','belief')),
  replaced_by        uuid references kernel.claim,     -- dcterms:isReplacedBy
  invalidated_by_run uuid references kernel.run,       -- prov:wasInvalidatedBy
  change_set         uuid not null references kernel.change_set,
  recorded_at        timestamptz not null default now()
);
create index on kernel.claim_status (claim_id, recorded_at desc);

create table kernel.belief (                  -- append-only; one model may score a claim many times
  claim_id    uuid not null references kernel.claim,
  model       text not null references kernel.entity,
  value       real not null check (value >= 0 and value <= 1),
  explanation jsonb not null default '{}',
  computed_by uuid not null references kernel.run,
  change_set  uuid not null references kernel.change_set,
  computed_at timestamptz not null default now()
);
create index on kernel.belief (claim_id, model, computed_at desc);

create table kernel.verification (
  claim_id    uuid not null references kernel.claim,
  verdict     text not null check (verdict in ('supported','refuted','inconclusive')),
  method      text not null references kernel.entity,   -- the verifier module
  run         uuid not null references kernel.run,
  details     jsonb not null default '{}',
  change_set  uuid not null references kernel.change_set,
  recorded_at timestamptz not null default now()
);
create index on kernel.verification (claim_id);

create table kernel.redaction (               -- erasure requests; the only path that alters stored content
  id          uuid primary key default gen_random_uuid(),
  target_kind text not null check (target_kind in ('claim','artifact')),
  target_id   text not null,
  reason      text not null,
  requested_by name not null default current_user,
  run         uuid not null references kernel.run,
  recorded_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- helpers
create function kernel.expand(term text) returns text
language sql stable as $$
  select case
    when term is null then null
    when term ~ '^[A-Za-z][A-Za-z0-9_-]*:' and term !~ '^(https?|urn):'
      then coalesce((select p.namespace || substr(term, length(p.prefix) + 2)
                     from kernel.prefix p where p.prefix = split_part(term, ':', 1)), term)
    else term
  end
$$;

create function kernel.my_agent() returns text
language sql stable as $$
  select iri from kernel.agent where db_role = current_user
$$;

-- Invoker-rights: evaluates membership of the *calling* role.
create function kernel.can_read(p_scope text) returns boolean
language sql stable as $$
  select exists (select 1 from kernel.scope_grant g
                 where g.scope = p_scope and g.can_read
                   and pg_has_role(current_user, g.db_role, 'member'))
$$;

create function kernel.role_can_write(p_role name, p_scope text) returns boolean
language sql stable as $$
  select exists (select 1 from kernel.scope_grant g
                 where g.scope = p_scope and g.can_write
                   and pg_has_role(p_role, g.db_role, 'member'))
$$;

-- True when every role that can read p_target can also read p_input.
-- A claim may only live in a scope at least as restrictive as everything it was made from.
create function kernel.readers_subset(p_target text, p_input text) returns boolean
language sql stable as $$
  select p_target = p_input or not exists (
    select 1 from kernel.scope_grant gt
    where gt.scope = p_target and gt.can_read
      and not exists (select 1 from kernel.scope_grant gs
                      where gs.scope = p_input and gs.can_read and gs.db_role = gt.db_role))
$$;

create function kernel.statement_key(p_subject text, p_predicate text, p_object_entity text,
                                     p_object_value jsonb, p_qualifiers jsonb) returns text
language sql immutable as $$
  select encode(sha256(convert_to(
    jsonb_build_array(p_subject, p_predicate, p_object_entity, p_object_value,
                      coalesce(p_qualifiers, '{}'::jsonb))::text, 'UTF8')), 'hex')
$$;

create function kernel.current_status(p_claim uuid) returns text
language sql stable as $$
  select coalesce((select status from kernel.claim_status
                   where claim_id = p_claim order by recorded_at desc limit 1), 'normal')
$$;

-- ---------------------------------------------------------------- immutability
create function kernel.forbid_mutation() returns trigger
language plpgsql as $$
begin
  if coalesce(current_setting('kernel.redacting', true), '') = 'on' and tg_op = 'UPDATE' then
    return new;
  end if;
  raise exception 'kernel: % on % is not allowed; records are append-only (use set_status or redaction)',
    tg_op, tg_table_name using errcode = 'KK001';
end $$;

do $$
declare t text;
begin
  foreach t in array array['claim','claim_reference','claim_derivation','claim_status',
                           'belief','verification','artifact','redaction','entity'] loop
    execute format('create trigger %I before update or delete on kernel.%I
                    for each row execute function kernel.forbid_mutation()', t || '_immutable', t);
  end loop;
end $$;

-- ---------------------------------------------------------------- run rules
create function kernel.run_before_write() returns trigger
language plpgsql as $$                         -- invoker rights: current_user is the real caller
begin
  if current_user = 'kk_owner' then return new; end if;
  if tg_op = 'INSERT' then
    new.agent := kernel.my_agent();
    if new.agent is null then
      raise exception 'kernel: role % is not mapped to an agent', current_user using errcode = 'KK002';
    end if;
    new.status := 'running';
    new.started_at := now();
    new.ended_at := null;
    return new;
  end if;
  -- UPDATE: only finishing your own running run
  if old.agent is distinct from kernel.my_agent() then
    raise exception 'kernel: runs can only be finished by their own agent' using errcode = 'KK003';
  end if;
  if old.status <> 'running' then
    raise exception 'kernel: run % is already finished', old.id using errcode = 'KK003';
  end if;
  if new.status = 'running' then
    raise exception 'kernel: finishing a run needs a final status' using errcode = 'KK003';
  end if;
  new.ended_at := coalesce(new.ended_at, now());
  return new;
end $$;

create trigger run_rules before insert or update on kernel.run
  for each row execute function kernel.run_before_write();

-- ---------------------------------------------------------------- change set rules
-- Lane upgrade: claims that change the vocabulary or mappings always go to review.
create function kernel.needs_review(p_ops jsonb) returns boolean
language sql stable as $$
  select exists (
    select 1 from jsonb_array_elements(p_ops) op
    where op->>'op' = 'add_claim'
      and (kernel.expand(op->>'predicate') in
             (select kernel.expand(x) from jsonb_array_elements_text(
                coalesce((select value from kernel.setting where key = 'review_predicates'), '[]')) x)
           or (kernel.expand(op->>'predicate') = kernel.expand('rdf:type')
               and kernel.expand(op->'object'->>'entity') in
                 (select kernel.expand(x) from jsonb_array_elements_text(
                    coalesce((select value from kernel.setting where key = 'review_types'), '[]')) x))))
$$;

create function kernel.change_set_before_insert() returns trigger
language plpgsql as $$                         -- invoker rights
declare v_run kernel.run;
begin
  new.submitted_by := current_user;
  new.target_scope := kernel.expand(new.target_scope);
  new.attributed_to := kernel.expand(new.attributed_to);
  new.submitted_at := now();
  new.validated_by := null; new.validation_report := null;
  new.decided_by := null; new.decided_at := null; new.applied_at := null;

  if current_user = 'kk_owner' then             -- bootstrap / migrations only
    return new;
  end if;
  new.status := 'submitted';

  if not exists (select 1 from kernel.agent where iri = new.attributed_to and db_role = current_user) then
    raise exception 'kernel: change set attributed to % but submitted by role %', new.attributed_to, current_user
      using errcode = 'KK010';
  end if;
  select * into v_run from kernel.run where id = new.generated_by;
  if v_run.agent is distinct from new.attributed_to then
    raise exception 'kernel: run % does not belong to %', new.generated_by, new.attributed_to
      using errcode = 'KK011';
  end if;
  if not kernel.role_can_write(current_user, new.target_scope) then
    raise exception 'kernel: role % has no write clearance for scope %', current_user, new.target_scope
      using errcode = 'KK012';
  end if;
  if kernel.needs_review(new.ops) then
    new.lane := 'review';
  end if;
  return new;
end $$;

create trigger change_set_insert_rules before insert on kernel.change_set
  for each row execute function kernel.change_set_before_insert();

create function kernel.change_set_before_update() returns trigger
language plpgsql as $$                         -- invoker rights; enforces the state machine
begin
  -- identity columns never change
  if (new.id, new.idempotency_key, new.submitted_by, new.attributed_to, new.generated_by,
      new.target_scope, new.lane, new.ops, new.submitted_at)
     is distinct from
     (old.id, old.idempotency_key, old.submitted_by, old.attributed_to, old.generated_by,
      old.target_scope, old.lane, old.ops, old.submitted_at) then
    raise exception 'kernel: a change set''s content cannot be edited' using errcode = 'KK020';
  end if;

  if current_user = 'kk_owner' then             -- the kernel itself (apply)
    if not (old.status in ('valid','approved') and new.status in ('applied','invalid','conflict'))
       and not (old.status = 'submitted' and new.status = 'approved') then   -- bootstrap
      raise exception 'kernel: transition % -> % not allowed', old.status, new.status using errcode = 'KK021';
    end if;
    return new;
  end if;

  if old.status = 'submitted' and new.status in ('valid','invalid') then
    if not pg_has_role(current_user, 'kk_validator', 'member') then
      raise exception 'kernel: only validators can validate' using errcode = 'KK022';
    end if;
    if current_user = old.submitted_by then
      raise exception 'kernel: a change set cannot be validated by its submitter' using errcode = 'KK023';
    end if;
    new.validated_by := current_user;
    if new.status = 'valid' and old.lane = 'review' then
      new.status := 'awaiting_review';
    end if;
    return new;
  end if;

  if old.status = 'awaiting_review' and new.status in ('approved','rejected') then
    if not pg_has_role(current_user, 'kk_reviewer', 'member') then
      raise exception 'kernel: only reviewers can decide' using errcode = 'KK024';
    end if;
    if current_user = old.submitted_by then
      raise exception 'kernel: a change set cannot be approved by its submitter' using errcode = 'KK025';
    end if;
    new.decided_by := current_user;
    new.decided_at := now();
    return new;
  end if;

  raise exception 'kernel: transition % -> % not allowed for role %', old.status, new.status, current_user
    using errcode = 'KK021';
end $$;

create trigger change_set_update_rules before update on kernel.change_set
  for each row execute function kernel.change_set_before_update();

-- ---------------------------------------------------------------- apply (the promoter core)
-- Applies every op of a change set atomically. Data problems mark the change set
-- 'invalid' (or 'conflict' for stale base versions) instead of raising, so the caller's
-- own update still commits and the report says what went wrong.
create function kernel.apply_change_set(p_id uuid) returns text
language plpgsql security definer set search_path = kernel, pg_catalog as $$
declare
  cs        kernel.change_set;
  op        jsonb;
  idx       int;
  refs      jsonb := '{}';
  v_claim   uuid;
  v_target  kernel.claim;
  v_subject text; v_predicate text; v_obj_entity text; v_obj_value jsonb; v_quals jsonb;
  v_input   uuid;
  r         jsonb;
  v_art     kernel.artifact;
  v_gen_run kernel.run;
  v_cs_run  kernel.run;
  v_state   text;
begin
  select * into cs from kernel.change_set where id = p_id for update;
  if cs.status not in ('valid','approved') then
    return cs.status;
  end if;
  select * into v_cs_run from kernel.run where id = cs.generated_by;

  begin  -- subtransaction: all ops or none
    for op, idx in select e, o from jsonb_array_elements(cs.ops) with ordinality as t(e, o) loop
      case op->>'op'

      when 'add_entity' then
        insert into kernel.entity(iri) values (kernel.expand(op->>'iri')) on conflict do nothing;

      when 'add_artifact' then
        insert into kernel.artifact(sha256, media_type, byte_size, location, scope, source_uri,
                                    retrieved_at, generated_by, change_set)
        values (op->>'sha256', op->>'media_type', (op->>'byte_size')::bigint, op->>'location',
                cs.target_scope, op->>'source_uri', (op->>'retrieved_at')::timestamptz,
                cs.generated_by, cs.id)
        on conflict (sha256) do nothing;

      when 'add_claim' then
        v_subject    := kernel.expand(op->>'subject');
        v_predicate  := kernel.expand(op->>'predicate');
        v_obj_entity := kernel.expand(op->'object'->>'entity');
        v_obj_value  := op->'object'->'value';
        v_quals      := coalesce(op->'qualifiers', '{}');

        if cs.submitted_by = 'kk_owner' then
          -- vocabulary packs and bootstrap run as migrations (reviewed as code)
          insert into kernel.entity(iri) values (v_predicate) on conflict do nothing;
        elsif not exists (select 1 from kernel.entity where iri = v_predicate) then
          raise exception 'op %: predicate % is not a known term', idx, v_predicate using errcode = 'KK030';
        elsif not kernel.is_approved_property(v_predicate) then
          raise exception 'op %: predicate % is not an approved property', idx, v_predicate using errcode = 'KK031';
        end if;
        insert into kernel.entity(iri) values (v_subject) on conflict do nothing;
        if v_obj_entity is not null then
          insert into kernel.entity(iri) values (v_obj_entity) on conflict do nothing;
        end if;

        if coalesce(jsonb_array_length(op->'references'), 0) = 0
           and coalesce(jsonb_array_length(op->'derived_from'), 0) = 0 then
          raise exception 'op %: a claim needs at least one reference or derived_from (invariant 1)', idx
            using errcode = 'KK032';
        end if;
        if op->>'evidence_type' = 'extracted' and not exists (
             select 1 from jsonb_array_elements(coalesce(op->'references', '[]')) x
             where coalesce(x->>'quote', '') <> '') then
          raise exception 'op %: extracted claims must carry the supporting quote (invariant 1)', idx
            using errcode = 'KK033';
        end if;

        insert into kernel.claim(subject, predicate, object_entity, object_value, qualifiers,
                                 valid_from, valid_to, scope, evidence_type, attributed_to,
                                 generated_by, change_set, statement_key)
        values (v_subject, v_predicate, v_obj_entity, v_obj_value, v_quals,
                (op->>'valid_from')::timestamptz, (op->>'valid_to')::timestamptz,
                cs.target_scope, op->>'evidence_type', cs.attributed_to, cs.generated_by, cs.id,
                kernel.statement_key(v_subject, v_predicate, v_obj_entity, v_obj_value, v_quals))
        returning id into v_claim;

        if op ? 'ref' then
          refs := refs || jsonb_build_object(op->>'ref', v_claim);
        end if;

        for r in select * from jsonb_array_elements(coalesce(op->'references', '[]')) loop
          if r ? 'artifact' then
            select * into v_art from kernel.artifact where sha256 = r->>'artifact';
            if v_art.sha256 is null then
              raise exception 'op %: unknown artifact %', idx, r->>'artifact' using errcode = 'KK034';
            end if;
            if not kernel.readers_subset(cs.target_scope, v_art.scope) then
              raise exception 'op %: artifact % is in a more restrictive scope than %', idx, v_art.sha256, cs.target_scope
                using errcode = 'KK035';
            end if;
          end if;
          insert into kernel.claim_reference(claim_id, artifact, source_uri, quote, quote_prefix,
                                             quote_suffix, span)
          values (v_claim, r->>'artifact', r->>'source_uri', r->>'quote', r->>'quote_prefix',
                  r->>'quote_suffix', r->'span');
        end loop;

        for r in select * from jsonb_array_elements(coalesce(op->'derived_from', '[]')) loop
          v_input := coalesce((refs->>(r#>>'{}'))::uuid, (r#>>'{}')::uuid);
          select * into v_target from kernel.claim where id = v_input;
          if v_target.id is null then
            raise exception 'op %: derived_from % not found', idx, r#>>'{}' using errcode = 'KK036';
          end if;
          if not kernel.readers_subset(cs.target_scope, v_target.scope) then
            raise exception 'op %: input claim % is in a more restrictive scope than % (invariant 3)',
              idx, v_input, cs.target_scope using errcode = 'KK037';
          end if;
          insert into kernel.claim_derivation(claim_id, derived_from) values (v_claim, v_input);
        end loop;

      when 'set_status' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        if op ? 'expect_status' and kernel.current_status(v_claim) <> op->>'expect_status' then
          raise exception 'op %: claim % status is %, expected %', idx, v_claim,
            kernel.current_status(v_claim), op->>'expect_status' using errcode = 'KK409';
        end if;
        if op->>'status' = 'deprecated' and op->>'reason' is null then
          raise exception 'op %: deprecating needs a reason', idx using errcode = 'KK041';
        end if;
        insert into kernel.claim_status(claim_id, status, reason, replaced_by, invalidated_by_run, change_set)
        values (v_claim, op->>'status', op->>'reason',
                coalesce((refs->>(op->>'replaced_by'))::uuid, (op->>'replaced_by')::uuid),
                case when op->>'reason' in ('refuted','retracted') then cs.generated_by end,
                cs.id);

      when 'add_belief' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        insert into kernel.entity(iri) values (kernel.expand(op->>'model')) on conflict do nothing;
        insert into kernel.belief(claim_id, model, value, explanation, computed_by, change_set)
        values (v_claim, kernel.expand(op->>'model'), (op->>'value')::real,
                coalesce(op->'explanation', '{}'), cs.generated_by, cs.id);

      when 'add_verification' then
        v_claim := coalesce((refs->>(op->>'claim'))::uuid, (op->>'claim')::uuid);
        select * into v_target from kernel.claim where id = v_claim;
        if v_target.id is null or v_target.scope <> cs.target_scope then
          raise exception 'op %: claim % not found in scope %', idx, op->>'claim', cs.target_scope
            using errcode = 'KK040';
        end if;
        select * into v_gen_run from kernel.run where id = v_target.generated_by;
        -- invariant 5: no method verifies its own output
        if v_gen_run.agent = v_cs_run.agent
           or (v_gen_run.module is not null and v_gen_run.module = v_cs_run.module)
           or kernel.expand(op->>'method') = v_gen_run.module then
          raise exception 'op %: claim % cannot be verified by the agent or module that produced it (invariant 5)',
            idx, v_claim using errcode = 'KK050';
        end if;
        insert into kernel.entity(iri) values (kernel.expand(op->>'method')) on conflict do nothing;
        insert into kernel.verification(claim_id, verdict, method, run, details, change_set)
        values (v_claim, op->>'verdict', kernel.expand(op->>'method'), cs.generated_by,
                coalesce(op->'details', '{}'), cs.id);

      else
        raise exception 'op %: unknown op %', idx, op->>'op' using errcode = 'KK039';
      end case;
    end loop;

    update kernel.change_set set status = 'applied', applied_at = now() where id = p_id;
    v_state := 'applied';
  exception when others then
    update kernel.change_set
       set status = case when sqlstate = 'KK409' then 'conflict' else 'invalid' end,
           validation_report = coalesce(validation_report, '{}'::jsonb)
                               || jsonb_build_object('apply_error', sqlerrm, 'sqlstate', sqlstate)
     where id = p_id;
    v_state := case when sqlstate = 'KK409' then 'conflict' else 'invalid' end;
  end;

  perform pg_notify('kernel_change_set',
                    jsonb_build_object('id', p_id, 'status', v_state, 'scope', cs.target_scope)::text);
  return v_state;
end $$;

-- A predicate is approved when a non-deprecated claim types it as a property.
-- Those typing claims always go through the review lane (see setting review_types).
create function kernel.is_approved_property(p_iri text) returns boolean
language sql stable security definer set search_path = kernel, pg_catalog as $$
  select exists (
    select 1 from kernel.claim c
    where c.subject = p_iri
      and c.predicate = kernel.expand('rdf:type')
      and c.object_entity in (kernel.expand('rdf:Property'), kernel.expand('owl:ObjectProperty'),
                              kernel.expand('owl:DatatypeProperty'), kernel.expand('owl:AnnotationProperty'))
      and kernel.current_status(c.id) <> 'deprecated')
$$;

create function kernel.change_set_after_update() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  perform kernel.apply_change_set(new.id);
  return null;
end $$;

create trigger change_set_apply after update of status on kernel.change_set
  for each row when (new.status in ('valid','approved') and old.status is distinct from new.status)
  execute function kernel.change_set_after_update();

create function kernel.change_set_after_insert() returns trigger
language plpgsql as $$
begin
  perform pg_notify('kernel_change_set',
                    jsonb_build_object('id', new.id, 'status', new.status, 'scope', new.target_scope,
                                       'lane', new.lane)::text);
  return null;
end $$;

create trigger change_set_notify after insert on kernel.change_set
  for each row execute function kernel.change_set_after_insert();

-- Migrations and vocabulary packs (run as kk_owner) write through the same apply path,
-- so even installed vocabulary is attributed, referenced and recorded as a change set.
create function kernel.install_ops(p_key text, p_scope text, p_ops jsonb,
                                   p_agent text default 'urn:kk:agent:kernel',
                                   p_module text default null) returns text
language plpgsql as $$
declare v_run uuid; v_cs uuid; v_state text;
begin
  if current_user <> 'kk_owner' then
    raise exception 'kernel.install_ops is for migrations run as kk_owner' using errcode = 'KK070';
  end if;
  select id into v_cs from kernel.change_set where idempotency_key = p_key;
  if v_cs is not null then
    return (select status from kernel.change_set where id = v_cs);
  end if;
  if p_module is not null then
    insert into kernel.entity(iri) values (kernel.expand(p_module)) on conflict do nothing;
  end if;
  insert into kernel.run(agent, module, status, ended_at)
  values (kernel.expand(p_agent), kernel.expand(p_module), 'succeeded', now()) returning id into v_run;
  insert into kernel.change_set(idempotency_key, attributed_to, generated_by, target_scope, lane, ops, status)
  values (p_key, p_agent, v_run, p_scope, 'review', p_ops, 'submitted') returning id into v_cs;
  update kernel.change_set set status = 'approved', decided_by = 'kk_owner', decided_at = now(),
                               review_note = 'installed by migration' where id = v_cs;
  select status into v_state from kernel.change_set where id = v_cs;
  if v_state <> 'applied' then
    raise exception 'install % failed: %', p_key,
      (select validation_report from kernel.change_set where id = v_cs) using errcode = 'KK071';
  end if;
  return v_state;
end $$;
revoke all on function kernel.install_ops(text, text, jsonb, text, text) from public;

revoke all on function kernel.apply_change_set(uuid) from public;
revoke all on function kernel.change_set_after_update() from public;

-- ---------------------------------------------------------------- redaction
create function kernel.redaction_before_insert() returns trigger
language plpgsql as $$                         -- invoker rights
begin
  if current_user <> 'kk_owner' and not pg_has_role(current_user, 'kk_steward', 'member') then
    raise exception 'kernel: only stewards can redact' using errcode = 'KK060';
  end if;
  new.requested_by := current_user;
  return new;
end $$;

create trigger redaction_rules before insert on kernel.redaction
  for each row execute function kernel.redaction_before_insert();

-- Keeps structure, identity and lineage; removes content. Recorded as its own run.
create function kernel.redaction_after_insert() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  perform set_config('kernel.redacting', 'on', true);
  if new.target_kind = 'claim' then
    update kernel.claim set object_value = null, object_entity = null, qualifiers = '{}',
                            redacted_at = now()
     where id = new.target_id::uuid;
    update kernel.claim_reference set quote = null, quote_prefix = null, quote_suffix = null, span = null
     where claim_id = new.target_id::uuid;
  else
    update kernel.artifact set redacted_at = now() where sha256 = new.target_id;
    update kernel.claim_reference set quote = null, quote_prefix = null, quote_suffix = null, span = null
     where artifact = new.target_id;
    -- The artifact store must delete the bytes; it listens on this channel.
  end if;
  perform set_config('kernel.redacting', 'off', true);
  perform pg_notify('kernel_redaction', jsonb_build_object('kind', new.target_kind, 'id', new.target_id)::text);
  return null;
end $$;

create trigger redaction_apply after insert on kernel.redaction
  for each row execute function kernel.redaction_after_insert();

-- ---------------------------------------------------------------- row-level security
alter table kernel.claim            enable row level security;
alter table kernel.claim_reference  enable row level security;
alter table kernel.claim_derivation enable row level security;
alter table kernel.claim_status     enable row level security;
alter table kernel.belief           enable row level security;
alter table kernel.verification     enable row level security;
alter table kernel.artifact         enable row level security;
alter table kernel.change_set       enable row level security;
alter table kernel.run              enable row level security;
alter table kernel.redaction        enable row level security;

create policy read_by_scope on kernel.claim for select using (kernel.can_read(scope));
create policy read_by_scope on kernel.artifact for select using (kernel.can_read(scope));
-- Dependent rows are visible when their claim is (the subquery is itself filtered by RLS).
create policy read_with_claim on kernel.claim_reference  for select using (exists (select 1 from kernel.claim c where c.id = claim_id));
create policy read_with_claim on kernel.claim_derivation for select using (exists (select 1 from kernel.claim c where c.id = claim_id));
create policy read_with_claim on kernel.claim_status     for select using (exists (select 1 from kernel.claim c where c.id = claim_id));
create policy read_with_claim on kernel.belief           for select using (exists (select 1 from kernel.claim c where c.id = claim_id));
create policy read_with_claim on kernel.verification     for select using (exists (select 1 from kernel.claim c where c.id = claim_id));

-- Validators and reviewers only see change sets for scopes they are cleared to read.
create policy own_or_control on kernel.change_set for select using (
  submitted_by = current_user
  or ((pg_has_role(current_user, 'kk_validator', 'member')
       or pg_has_role(current_user, 'kk_reviewer', 'member'))
      and kernel.can_read(target_scope)));
create policy submit on kernel.change_set for insert with check (submitted_by = current_user);
create policy control on kernel.change_set for update using (
  kernel.can_read(target_scope)
  and ((status = 'submitted' and pg_has_role(current_user, 'kk_validator', 'member'))
       or (status = 'awaiting_review' and pg_has_role(current_user, 'kk_reviewer', 'member'))))
  with check (pg_has_role(current_user, 'kk_validator', 'member')
              or pg_has_role(current_user, 'kk_reviewer', 'member'));  -- transitions enforced by trigger

create policy read_runs on kernel.run for select using (true);
create policy start_run on kernel.run for insert with check (agent = kernel.my_agent());
create policy finish_own on kernel.run for update using (agent = kernel.my_agent());

create policy stewards on kernel.redaction for all using (pg_has_role(current_user, 'kk_steward', 'member'))
  with check (pg_has_role(current_user, 'kk_steward', 'member'));

-- ---------------------------------------------------------------- privileges
grant usage on schema kernel to kk_agent;
grant select on kernel.prefix, kernel.setting, kernel.agent, kernel.scope, kernel.scope_grant,
                kernel.entity, kernel.run, kernel.claim, kernel.claim_reference, kernel.claim_derivation,
                kernel.claim_status, kernel.belief, kernel.verification, kernel.artifact,
                kernel.change_set to kk_agent;
grant insert (iri) on kernel.entity to kk_agent;     -- identifiers only; meaning comes from claims
grant insert (module, plan_step, used, info) on kernel.run to kk_agent;
grant update (status, ended_at, cost, info) on kernel.run to kk_agent;
grant insert (idempotency_key, attributed_to, generated_by, target_scope, lane, ops)
  on kernel.change_set to kk_agent;
grant update (status, validation_report, validated_by) on kernel.change_set to kk_validator;
grant update (status, decided_by, decided_at, review_note) on kernel.change_set to kk_reviewer;
grant select, insert (target_kind, target_id, reason, run) on kernel.redaction to kk_steward;

-- ---------------------------------------------------------------- api schema (what PostgREST exposes)
create view api.claims with (security_invoker = true) as
select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers,
       c.valid_from, c.valid_to, c.scope, c.evidence_type, c.attributed_to, c.generated_by,
       c.change_set, c.statement_key, c.recorded_at, c.redacted_at,
       kernel.current_status(c.id) as status,
       (select b.value from kernel.belief b
         where b.claim_id = c.id
           and b.model = kernel.expand((select value #>> '{}' from kernel.setting where key = 'active_belief_model'))
         order by b.computed_at desc limit 1) as belief
from kernel.claim c;

create view api.change_sets with (security_invoker = true) as
select id, idempotency_key, submitted_by, attributed_to, generated_by, target_scope, lane, status,
       validated_by, validation_report, decided_by, decided_at, review_note, submitted_at, applied_at,
       jsonb_array_length(ops) as op_count
from kernel.change_set;

create view api.review_queue with (security_invoker = true) as
select id, attributed_to, target_scope, ops, validation_report, submitted_at
from kernel.change_set where status = 'awaiting_review';

create function api.start_run(module text default null, plan_step text default null,
                              used jsonb default '[]', info jsonb default '{}')
returns uuid language plpgsql as $$
declare v uuid;
begin
  if module is not null then
    insert into kernel.entity(iri) select kernel.expand(module)
      where not exists (select 1 from kernel.entity where iri = kernel.expand(module));
  end if;
  insert into kernel.run(module, plan_step, used, info)
  values (kernel.expand(module), kernel.expand(plan_step), used, info) returning id into v;
  return v;
end $$;

create function api.finish_run(run_id uuid, status text, cost jsonb default '{}')
returns void language plpgsql as $$
begin
  update kernel.run set status = finish_run.status, cost = finish_run.cost where id = run_id;
  if not found then
    raise exception 'kernel: run % not found among your running runs', run_id using errcode = 'KK003';
  end if;
end $$;

-- Submit a change set. Idempotent on idempotency_key for the same submitter.
create function api.submit_change_set(cs jsonb) returns jsonb
language plpgsql as $$
declare v kernel.change_set;
begin
  select * into v from kernel.change_set c where c.idempotency_key = cs->>'idempotency_key';
  if v.id is null then
    insert into kernel.change_set(idempotency_key, attributed_to, generated_by, target_scope, lane, ops)
    values (cs->>'idempotency_key', cs->>'attributed_to', (cs->>'generated_by')::uuid,
            cs->>'target_scope', coalesce(cs->>'lane', 'auto'), cs->'ops')
    returning * into v;
  end if;
  return jsonb_build_object('id', v.id, 'status', v.status, 'lane', v.lane);
end $$;

create function api.record_validation(change_set_id uuid, ok boolean, report jsonb default '{}')
returns text language plpgsql as $$
declare v text;
begin
  update kernel.change_set set status = case when ok then 'valid' else 'invalid' end,
                               validation_report = report
   where id = change_set_id;
  select status into v from kernel.change_set where id = change_set_id;
  return v;
end $$;

create function api.review(change_set_id uuid, approve boolean, note text default null)
returns text language plpgsql as $$
declare v text;
begin
  update kernel.change_set set status = case when approve then 'approved' else 'rejected' end,
                               review_note = note
   where id = change_set_id;
  select status into v from kernel.change_set where id = change_set_id;
  return v;
end $$;

create function api.redact(target_kind text, target_id text, reason text, run uuid)
returns uuid language sql as $$
  insert into kernel.redaction(target_kind, target_id, reason, run)
  values (target_kind, target_id, reason, run) returning id
$$;

-- Everything needed to explain a claim: sources, lineage, status history, belief, verification.
create function api.explain(claim_id uuid) returns jsonb
language sql stable as $$
  select jsonb_build_object(
    'claim',        (select to_jsonb(c) from api.claims c where c.id = claim_id),
    'references',   coalesce((select jsonb_agg(to_jsonb(r) - 'claim_id') from kernel.claim_reference r where r.claim_id = explain.claim_id), '[]'),
    'derived_from', coalesce((select jsonb_agg(d.derived_from) from kernel.claim_derivation d where d.claim_id = explain.claim_id), '[]'),
    'used_by',      coalesce((select jsonb_agg(d.claim_id) from kernel.claim_derivation d where d.derived_from = explain.claim_id), '[]'),
    'status',       coalesce((select jsonb_agg(to_jsonb(s) - 'claim_id' order by s.recorded_at) from kernel.claim_status s where s.claim_id = explain.claim_id), '[]'),
    'beliefs',      coalesce((select jsonb_agg(to_jsonb(b) - 'claim_id' order by b.computed_at) from kernel.belief b where b.claim_id = explain.claim_id), '[]'),
    'verifications',coalesce((select jsonb_agg(to_jsonb(v) - 'claim_id') from kernel.verification v where v.claim_id = explain.claim_id), '[]'),
    'run',          (select to_jsonb(r) from kernel.run r join kernel.claim c on c.generated_by = r.id where c.id = explain.claim_id))
$$;

grant usage on schema api to kk_agent;
grant select on api.claims, api.change_sets to kk_agent;
grant select on api.review_queue to kk_reviewer;
grant execute on function api.start_run(text, text, jsonb, jsonb), api.finish_run(uuid, text, jsonb),
                          api.submit_change_set(jsonb), api.explain(uuid) to kk_agent;
grant execute on function api.record_validation(uuid, boolean, jsonb) to kk_validator;
grant execute on function api.review(uuid, boolean, text) to kk_reviewer;
grant execute on function api.redact(text, text, text, uuid) to kk_steward;
revoke execute on function api.record_validation(uuid, boolean, jsonb), api.review(uuid, boolean, text),
                           api.redact(text, text, text, uuid) from public;

reset role;

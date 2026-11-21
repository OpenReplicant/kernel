-- Kernel v0.1 (M6-T0, D-86 confirmed by D-87, D-109): the only two additions to the frozen kernel.
--   kernel.current_edge   one row per current claim whose object is an entity, kept in step by triggers
--                         inside the apply transaction (and redaction's), so a graph engine or a detector
--                         reads edges without filtering every claim; row-level security as claims have.
--   kernel.applied        the order change sets were applied in, a cursor: api.applied (seq > n).
-- Safe to run again, on a fresh kernel (after 001 and 002) or on one made before this file (kk init applies
-- it to both); an existing kernel's edges and applied order are filled in from what is there.
set role kk_owner;

-- ---------------------------------------------------------------- the edge table
create table if not exists kernel.current_edge (
  claim_id    uuid primary key references kernel.claim,
  subject     text not null,
  predicate   text not null,
  object      text not null,
  qualifiers  jsonb not null,
  valid_from  timestamptz,
  valid_to    timestamptz,
  scope       text not null,
  status      text not null check (status in ('normal','preferred')),   -- deprecated claims have no edge
  change_set  uuid not null                                              -- the change set that last changed it
);
create index if not exists current_edge_subject on kernel.current_edge (subject, predicate);
create index if not exists current_edge_object  on kernel.current_edge (object, predicate);
create index if not exists current_edge_scope   on kernel.current_edge (scope);

-- Applies (and redactions) run one at a time, from their start to their commit, under one lock only the kernel
-- can take (agents hold SELECT only, which LOCK TABLE ... SHARE ROW EXCLUSIVE does not allow). One lock, taken
-- first, means applies cannot deadlock on each other's edge rows, and it gives kernel.applied its commit order.
-- Applies were already cheap SQL (validation runs before, outside); this trades their parallelism for that.
create or replace function kernel.serialize() returns void
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  lock table kernel.applied in share row exclusive mode;   -- self-conflicting; readers' ACCESS SHARE still reads
end $$;
revoke all on function kernel.serialize() from public;

-- Recomputes one claim's edge from the claim and its latest status: inserted, updated or removed.
create or replace function kernel.refresh_edge(p_claim uuid, p_change_set uuid) returns void
language plpgsql security definer set search_path = kernel, pg_catalog as $$
declare
  c kernel.claim;
  s text;
begin
  -- no lock here: every caller holds kernel.serialize()'s (an apply, or a redaction), so edges change one
  -- transaction at a time and a second change to a claim reads the first's committed row
  select * into c from kernel.claim where id = p_claim;
  s := kernel.current_status(p_claim);
  if c.id is null or c.object_entity is null or c.redacted_at is not null or s = 'deprecated' then
    delete from kernel.current_edge where claim_id = p_claim;
    return;
  end if;
  insert into kernel.current_edge (claim_id, subject, predicate, object, qualifiers, valid_from, valid_to,
                                   scope, status, change_set)
  values (c.id, c.subject, c.predicate, c.object_entity, c.qualifiers, c.valid_from, c.valid_to,
          c.scope, s, coalesce(p_change_set, c.change_set))
  on conflict (claim_id) do update
    set status = excluded.status, change_set = excluded.change_set;   -- the claim itself never changes
end $$;
revoke all on function kernel.refresh_edge(uuid, uuid) from public;

create or replace function kernel.edge_after_claim() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  if tg_op = 'INSERT' then
    if new.object_entity is not null then          -- a value claim has no edge: nothing to do, nothing to lock
      perform kernel.refresh_edge(new.id, new.change_set);
    end if;
  else                                             -- the claim's only update is redaction
    perform kernel.serialize();
    perform kernel.refresh_edge(new.id, null);
  end if;
  return null;
end $$;

create or replace function kernel.edge_after_status() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  perform kernel.refresh_edge(new.claim_id, new.change_set);
  return null;
end $$;
revoke all on function kernel.edge_after_claim() from public;
revoke all on function kernel.edge_after_status() from public;

-- A claim's insert (apply) and its only update (redaction) both reach the edge in the same transaction.
drop trigger if exists claim_edge on kernel.claim;
create trigger claim_edge after insert or update on kernel.claim
  for each row execute function kernel.edge_after_claim();
drop trigger if exists claim_status_edge on kernel.claim_status;
create trigger claim_status_edge after insert on kernel.claim_status
  for each row execute function kernel.edge_after_status();

alter table kernel.current_edge enable row level security;
drop policy if exists read_by_scope on kernel.current_edge;
create policy read_by_scope on kernel.current_edge for select using (kernel.can_read(scope));
revoke all on kernel.current_edge from public;
grant select on kernel.current_edge to kk_agent;

-- Fill in an existing kernel's edges (a no-op on a fresh one).
do $$
declare r record;
begin
  for r in select c.id from kernel.claim c
           where c.object_entity is not null and c.redacted_at is null
             and not exists (select 1 from kernel.current_edge e where e.claim_id = c.id) loop
    perform kernel.refresh_edge(r.id, (select st.change_set from kernel.claim_status st where st.claim_id = r.id
                                        order by st.recorded_at desc limit 1));
  end loop;
end $$;

create or replace view api.edges with (security_invoker = true) as
select claim_id, subject, predicate, object, qualifiers, valid_from, valid_to, scope, status, change_set
from kernel.current_edge;
grant select on api.edges to kk_agent;

-- ---------------------------------------------------------------- the applied order
-- One row per applied change set, numbered in the order they were applied: the cursor a detector keeps.
-- Read through row-level security by the change set's scope (a reader sees that a change set it is cleared
-- for was applied, never more; its content is read as claims). Append-only, like the claims.
create table if not exists kernel.applied (
  seq          bigint generated always as identity primary key,
  change_set   uuid not null unique references kernel.change_set,
  target_scope text not null,
  applied_at   timestamptz not null
);

-- kernel.serialize()'s lock makes numbering and commit happen in the same order: an apply numbered n+1 cannot
-- commit before n has, so a reader that has seen n never misses one numbered below it later.
create or replace function kernel.change_set_number() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  perform kernel.serialize();                      -- already held since the apply began; taken again for safety
  insert into kernel.applied (change_set, target_scope, applied_at)
  values (new.id, new.target_scope, coalesce(new.applied_at, now()));
  return null;
end $$;
revoke all on function kernel.change_set_number() from public;
drop trigger if exists change_set_number on kernel.change_set;
create trigger change_set_number after update of status on kernel.change_set
  for each row when (new.status = 'applied' and old.status is distinct from 'applied')
  execute function kernel.change_set_number();

-- Taken when a change set is about to be applied, before change_set_apply runs it (after-triggers fire in name
-- order: change_set_a0_serialize, then change_set_apply), under the same condition as change_set_apply.
create or replace function kernel.change_set_serialize() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  perform kernel.serialize();
  return null;
end $$;
revoke all on function kernel.change_set_serialize() from public;
drop trigger if exists change_set_a0_serialize on kernel.change_set;
create trigger change_set_a0_serialize after update of status on kernel.change_set
  for each row when (new.status in ('valid','approved') and old.status is distinct from new.status)
  execute function kernel.change_set_serialize();

drop trigger if exists applied_immutable on kernel.applied;
create trigger applied_immutable before update or delete on kernel.applied
  for each row execute function kernel.forbid_mutation();

alter table kernel.applied enable row level security;
drop policy if exists read_by_scope on kernel.applied;
create policy read_by_scope on kernel.applied for select using (kernel.can_read(target_scope));
revoke all on kernel.applied from public;
grant select on kernel.applied to kk_agent;

-- Number an existing kernel's applied change sets in the order they were applied (a no-op on a fresh one).
insert into kernel.applied (change_set, target_scope, applied_at)
select c.id, c.target_scope, c.applied_at from kernel.change_set c
where c.status = 'applied' and not exists (select 1 from kernel.applied a where a.change_set = c.id)
order by c.applied_at, c.submitted_at, c.ctid;   -- ctid: insertion order within one transaction, near enough

-- What was applied after a cursor, in order: `select * from api.applied where seq > $1 order by seq`.
create or replace view api.applied with (security_invoker = true) as
select seq, change_set, target_scope, applied_at from kernel.applied;
grant select on api.applied to kk_agent;

reset role;

-- D-83 spike: a live graph and detectors inside the kernel's apply transaction (Apache AGE 1.7.0).
-- Scratch only: loaded after 001_kernel.sql, 002_bootstrap.sql and packs.sql into a throwaway database.
-- Needs shared_preload_libraries = 'age'. Run as a superuser.

create extension age;
set search_path = ag_catalog, public;
select create_graph('kkg');
select create_vlabel('kkg', 'Entity');
select create_elabel('kkg', 'Claim');
reset search_path;
create unique index entity_iri on kkg."Entity" (ag_catalog.agtype_access_operator(properties, '"iri"'::ag_catalog.agtype));
create index entity_props on kkg."Entity" using gin (properties);
create index claim_id on kkg."Claim" (ag_catalog.agtype_access_operator(properties, '"id"'::ag_catalog.agtype));

-- The kernel (kk_owner) writes the graph; everyone else may only read it, under row-level security.
grant usage on schema kkg, ag_catalog to kk_owner, kk_agent;
grant all on all tables in schema kkg to kk_owner;
grant all on all sequences in schema kkg to kk_owner;
grant select on all tables in schema kkg to kk_agent;
alter table kkg."Claim" enable row level security;
alter table kkg."Claim" force row level security;
create policy kernel_writes on kkg."Claim" for all to kk_owner using (true) with check (true);
create policy read_by_scope on kkg."Claim" for select to kk_agent
  using (kernel.can_read(ag_catalog.agtype_access_operator(properties, '"scope"'::ag_catalog.agtype)::text));
-- (entities carry no scope in v0: kernel.entity is readable by every agent, so vertices are too)

set role kk_owner;

-- What changed in this transaction, for the detectors that run at commit.
create table kernel.graph_touched (
  xid      xid8 not null default pg_current_xact_id(),
  iri      text,                -- a vertex whose edges changed
  claim    uuid,                -- a claim added (+) or removed (-)
  sign     smallint,
  artifact text                 -- an artifact added
);
create index on kernel.graph_touched (xid);

-- ---------------------------------------------------------------- sync
create function kernel.graph_add(c kernel.claim) returns void
language plpgsql security definer set search_path = kernel, ag_catalog, pg_catalog as $$
begin
  insert into kernel.graph_touched(claim, sign) values (c.id, 1);
  if c.object_entity is null or c.redacted_at is not null or kernel.current_status(c.id) = 'deprecated' then
    return;
  end if;
  -- Direct writes to the label tables (what AGE's own loaders do): an indexed upsert per vertex, one edge row.
  insert into kkg."Entity"(properties)
  values (agtype_build_map('iri', c.subject)), (agtype_build_map('iri', c.object_entity))
  on conflict ((agtype_access_operator(properties, '"iri"'::agtype))) do nothing;
  insert into kkg."Claim"(start_id, end_id, properties)
  select s.id, o.id, agtype_build_map('id', c.id::text, 'p', c.predicate, 'scope', c.scope, 'evidence', c.evidence_type)
    from kkg."Entity" s, kkg."Entity" o
   where agtype_access_operator(s.properties, '"iri"'::agtype) = to_jsonb(c.subject)::text::agtype
     and agtype_access_operator(o.properties, '"iri"'::agtype) = to_jsonb(c.object_entity)::text::agtype;
  insert into kernel.graph_touched(iri) values (c.subject), (c.object_entity);
end $$;

create function kernel.graph_remove(p_claim uuid) returns void
language plpgsql security definer set search_path = kernel, ag_catalog, pg_catalog as $$
begin
  with gone as (
    delete from kkg."Claim" e
     where agtype_access_operator(e.properties, '"id"'::agtype) = to_jsonb(p_claim::text)::text::agtype
    returning e.start_id, e.end_id)
  insert into kernel.graph_touched(iri)
  select agtype_access_operator(v.properties, '"iri"'::agtype)::text
    from gone g join kkg."Entity" v on v.id in (g.start_id, g.end_id);
  insert into kernel.graph_touched(claim, sign) values (p_claim, -1);
end $$;

create function kernel.claim_graph() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  if tg_table_name = 'claim' and tg_op = 'INSERT' then
    perform kernel.graph_add(new);
  elsif tg_table_name = 'claim' then                 -- redaction
    perform kernel.graph_remove(new.id);
  else                                               -- a status change: drop the edge, re-add if still current
    perform kernel.graph_remove(new.claim_id);
    perform kernel.graph_add(c) from kernel.claim c where c.id = new.claim_id;
  end if;
  return null;
end $$;

create trigger claim_graph after insert on kernel.claim for each row execute function kernel.claim_graph();
create trigger claim_graph_redact after update of redacted_at on kernel.claim for each row
  when (old.redacted_at is null and new.redacted_at is not null) execute function kernel.claim_graph();
create trigger claim_status_graph after insert on kernel.claim_status for each row execute function kernel.claim_graph();

create function kernel.artifact_touch() returns trigger
language plpgsql security definer set search_path = kernel, pg_catalog as $$
begin
  insert into kernel.graph_touched(artifact) values (new.sha256);
  return null;
end $$;
create trigger artifact_touch after insert on kernel.artifact for each row execute function kernel.artifact_touch();

-- ---------------------------------------------------------------- detectors and events
-- A detector is data. kind 'artifact': {"media_type": "text/%"}; kind 'claim': {"s":…, "p":…, "o":…} (null = any);
-- kind 'shape': an openCypher query over kkg taking $anchor (one IRI whose edges changed; the kernel runs it once
-- per such IRI) and returning key (one string per match), nodes (every vertex whose edges the pattern reads) and
-- claims (the edge ids). A pattern of several nodes anchors each in its own UNION branch.
create table kernel.detector (
  iri      text primary key,
  kind     text not null check (kind in ('artifact', 'claim', 'shape')),
  pattern  jsonb,
  cypher   text check (cypher not like '%$cy$%')   -- AGE takes the query as a dollar-quoted constant
);
create table kernel.detector_match (          -- the shape matches that currently hold
  detector text not null references kernel.detector,
  key      text not null,
  nodes    text[] not null,
  claims   uuid[] not null,
  primary key (detector, key)
);
create index on kernel.detector_match using gin (nodes);

-- Registering a shape detector compiles its query into a function, so its plan is prepared once per session.
create function kernel.detector_compile() returns trigger
language plpgsql security definer set search_path = kernel, ag_catalog, pg_catalog as $fn$
begin
  if new.kind = 'shape' then
    execute format($f$create or replace function kernel.%I(p ag_catalog.agtype)
                      returns table (key text, nodes text[], claims uuid[])
                      language plpgsql stable set search_path = ag_catalog, pg_catalog as $b$
                      begin
                        return query
                        select k::text, array(select jsonb_array_elements_text(n::text::jsonb)),
                               array(select jsonb_array_elements_text(c::text::jsonb))::uuid[]
                          from ag_catalog.cypher('kkg', $cy$%s$cy$, p) as (k agtype, n agtype, c agtype);
                      end $b$ $f$, 'detector_' || md5(new.iri), new.cypher);
  end if;
  return new;
end $fn$;
create trigger detector_compile before insert or update on kernel.detector
  for each row execute function kernel.detector_compile();
create table kernel.event (                   -- ids only; a reactor reads what its clearance allows
  id       bigserial primary key,
  detector text not null references kernel.detector,
  kind     text not null check (kind in ('formed', 'dissolved')),
  key      text not null,
  nodes    text[] not null default '{}',
  claims   uuid[] not null default '{}',
  artifact text,
  at       timestamptz not null default now()
);
alter table kernel.event enable row level security;
create policy read_if_cleared on kernel.event for select using (
  not exists (select 1 from unnest(claims) x where not exists (select 1 from kernel.claim c where c.id = x))
  and (artifact is null or exists (select 1 from kernel.artifact a where a.sha256 = artifact)));
grant select on kernel.event, kernel.detector to kk_agent;

create function kernel.emit(d text, k text, key text, nodes text[], claims uuid[], art text default null)
returns void language plpgsql as $$
declare v bigint;
begin
  insert into kernel.event(detector, kind, key, nodes, claims, artifact) values (d, k, key, nodes, claims, art)
  returning id into v;
  perform pg_notify('kernel_event', json_build_object('id', v, 'detector', d, 'kind', k)::text);
end $$;

create function kernel.detect() returns trigger
language plpgsql security definer set search_path = kernel, ag_catalog, pg_catalog as $$
declare
  anchors text[];
  d       kernel.detector;
  m       record;
begin
  if not exists (select 1 from kernel.graph_touched where xid = pg_current_xact_id()) then
    return null;                                     -- an earlier firing in this transaction did the work
  end if;
  select coalesce(array_agg(distinct iri) filter (where iri is not null), '{}') into anchors
    from kernel.graph_touched where xid = pg_current_xact_id();

  for d in select * from kernel.detector loop
    if d.kind = 'artifact' then
      perform kernel.emit(d.iri, 'formed', a.sha256, '{}', '{}', a.sha256)
         from kernel.graph_touched t join kernel.artifact a on a.sha256 = t.artifact
        where t.xid = pg_current_xact_id() and a.media_type like d.pattern->>'media_type';
    elsif d.kind = 'claim' then
      perform kernel.emit(d.iri, case when t.sign > 0 then 'formed' else 'dissolved' end, c.id::text,
                          array_remove(array[c.subject, c.object_entity], null), array[c.id])
         from kernel.graph_touched t join kernel.claim c on c.id = t.claim
        where t.xid = pg_current_xact_id()
          and (d.pattern->>'s' is null or c.subject = d.pattern->>'s')
          and (d.pattern->>'p' is null or c.predicate = d.pattern->>'p')
          and (d.pattern->>'o' is null or c.object_entity = d.pattern->>'o')
          and (t.sign < 0 or (kernel.current_status(c.id) <> 'deprecated' and c.redacted_at is null));
    elsif cardinality(anchors) > 0 then
      create temp table if not exists pg_temp.cur (key text, nodes text[], claims uuid[]) on commit drop;
      truncate pg_temp.cur;
      -- Once per changed vertex, through the detector's compiled function (its plan is cached per session):
      -- AGE plans `x.iri = $anchor` on the iri index; `IN $list`, UNWIND or an OR across nodes scan every edge.
      execute format('insert into pg_temp.cur select f.* from unnest($1) a, kernel.%I(json_build_object(''anchor'', a)::text::ag_catalog.agtype) f',
                     'detector_' || md5(d.iri))
        using anchors;
      for m in select distinct on (key) * from pg_temp.cur c
                where not exists (select 1 from kernel.detector_match x where x.detector = d.iri and x.key = c.key) loop
        insert into kernel.detector_match values (d.iri, m.key, m.nodes, m.claims);
        perform kernel.emit(d.iri, 'formed', m.key, m.nodes, m.claims);
      end loop;
      for m in delete from kernel.detector_match x
                where x.detector = d.iri and x.nodes && anchors
                  and not exists (select 1 from pg_temp.cur c where c.key = x.key)
               returning * loop
        perform kernel.emit(d.iri, 'dissolved', m.key, m.nodes, m.claims);
      end loop;
    end if;
  end loop;
  delete from kernel.graph_touched where xid = pg_current_xact_id();
  return null;
end $$;

-- At commit, once per transaction (later firings find nothing left to do).
create constraint trigger detect after insert on kernel.graph_touched
  deferrable initially deferred for each row execute function kernel.detect();

reset role;

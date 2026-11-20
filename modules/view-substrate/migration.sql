-- Module install: view-substrate (D-104). Runs once as a superuser; see modules/_template/migration.sql.
--
-- Substrate's read model over the kernel's claims: what its UI and agent tools read from Substrate's own store
-- (store.usable, store.search, store.render, store.map, store.map_nodes, store.alternatives; plan step 3d there),
-- answered from the claims encoder-substrate wrote (D-103), in the module's own schema `substrate`.
--
-- Only views and functions: no tables, no writes. Every view is `security_invoker` and every function `security
-- invoker`, and they read only `api.claims` and `api.claim_references`, so the kernel's row-level security applies
-- to whoever calls: a claim in a scope the caller may not read is not in any answer. Nothing here is `security
-- definer`. Clearance comes from the default scope grants to kk_agent (kernel/schema/002_bootstrap.sql).
--
-- Terms (encoder-substrate): items are urn:kk:substrate:<Qid> or a pack IRI; labels rdfs:label, descriptions
-- dcterms:description, aliases skos:altLabel; has part wdt:P527 with the role as the qualifier wdt:P3831;
-- substrate:requiresInterface and substrate:providesInterface; rank is the claim's status.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_view_substrate') then
    create role kkm_view_substrate nologin;
  end if;
end $$;
grant kk_agent to kkm_view_substrate;
grant kkm_view_substrate to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:view-substrate', 'kkm_view_substrate', 'software', 'Substrate read model')
on conflict (iri) do nothing;
-- References are read through api.claim_references (the verifier module makes it; same definition).
do $$ begin
  if to_regclass('api.claim_references') is null then
    execute 'create view api.claim_references with (security_invoker = true) as
             select claim_id, artifact, source_uri, quote, span from kernel.claim_reference';
    execute 'grant select on api.claim_references to kk_agent';
  end if;
end $$;
reset role;

-- Trigram similarity ranks search as Substrate's store does (pg_trgm, a contrib extension). Where it cannot be
-- installed, substrate.sim falls back to a substring ratio: the same matches, a coarser rank.
do $$ begin
  begin
    create extension if not exists pg_trgm;
  exception when others then
    raise notice 'view-substrate: pg_trgm is not available (%); search ranks by substring ratio', sqlerrm;
  end;
end $$;

create schema if not exists substrate authorization kkm_view_substrate;
set role kkm_view_substrate;

-- Terms ---------------------------------------------------------------------------------------------------

create or replace function substrate.term(p_name text) returns text language sql immutable parallel safe as $$
  select case p_name
    when 'label'       then 'http://www.w3.org/2000/01/rdf-schema#label'
    when 'description' then 'http://purl.org/dc/terms/description'
    when 'alt'         then 'http://www.w3.org/2004/02/skos/core#altLabel'
    when 'has-part'    then 'http://www.wikidata.org/prop/direct/P527'
    when 'role'        then 'http://www.wikidata.org/prop/direct/P3831'
    when 'requires'    then 'https://w3id.org/knowledge-kernel/substrate#requiresInterface'
    when 'provides'    then 'https://w3id.org/knowledge-kernel/substrate#providesInterface'
  end $$;

-- The short form of an IRI: the Qid of urn:kk:substrate:Q13, else what follows the last # / or :.
create or replace function substrate.short(p_iri text) returns text language sql immutable parallel safe as $$
  select case when p_iri like 'urn:kk:substrate:%' then substr(p_iri, 18)
              else regexp_replace(p_iri, '^.*[#/:]', '') end $$;

-- Substrate's own items and the terms of its packs (wikidata, substrate, backstage: the v1 import's relations): what search looks through.
create or replace function substrate.in_namespace(p_iri text) returns boolean language sql immutable parallel safe as $$
  select p_iri like 'urn:kk:substrate:%' or p_iri like 'http://www.wikidata.org/%'
      or p_iri like 'https://w3id.org/knowledge-kernel/substrate#%'
      or p_iri like 'https://w3id.org/knowledge-kernel/backstage#%' $$;

-- The values of one qualifier: encoder-substrate keys a qualifier by its predicate IRI, a repeated one a list.
create or replace function substrate.qvals(p_qualifiers jsonb, p_property text)
returns table (value text, n bigint) language sql immutable parallel safe as $$
  select v.value, v.n from jsonb_array_elements_text(
    case when jsonb_typeof(p_qualifiers -> p_property) = 'array' then p_qualifiers -> p_property
         when p_qualifiers ? p_property then jsonb_build_array(p_qualifiers -> p_property)
         else '[]'::jsonb end) with ordinality as v(value, n) $$;

-- Percent-decoding, for the names in a reference's IRI.
create or replace function substrate.urldecode(p_text text) returns text language sql immutable parallel safe as $$
  select convert_from(decode(coalesce(string_agg(
           case when m[1] is not null then substr(m[1], 2) else encode(convert_to(m[2], 'UTF8'), 'hex') end, '' order by n),
           ''), 'hex'), 'UTF8')
  from regexp_matches(p_text, '(%[0-9A-Fa-f]{2})|([^%]+|%)', 'g') with ordinality as x(m, n) $$;

do $$ begin
  if to_regprocedure('public.similarity(text,text)') is not null then
    execute $f$create or replace function substrate.sim(a text, b text) returns real
      language sql immutable parallel safe set search_path = pg_catalog, public as
      $b$ select similarity(a, b) $b$
    $f$;
  else
    execute $f$create or replace function substrate.sim(a text, b text) returns real
      language sql immutable parallel safe set search_path = pg_catalog as
      $b$ select case when position(lower(b) in lower(a)) > 0 then least(1, length(b)::real / greatest(length(a), 1))
                      else 0 end::real $b$
    $f$;
  end if;
end $$;

-- The usable value -------------------------------------------------------------------------------------------
-- Wikidata's rule, as store.usable has it: per subject and property the preferred claims if there are any, else
-- the normal ones; never deprecated (superseded and retracted claims are deprecated too). Current only: not
-- redacted, and inside valid_from and valid_to. The rank is the claim's status.
create or replace view substrate.usable with (security_invoker = true) as
select x.id, x.subject, x.predicate, x.object_entity, x.object_value, x.qualifiers, x.rank, x.belief, x.scope,
       x.statement_key, x.recorded_at
from (
  select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.status as rank, c.belief,
         c.scope, c.statement_key, c.recorded_at,
         bool_or(c.status = 'preferred') over (partition by c.subject, c.predicate) as has_preferred
  from api.claims c
  where c.status in ('normal', 'preferred') and c.redacted_at is null
    and (c.valid_from is null or c.valid_from <= now()) and (c.valid_to is null or c.valid_to > now())
) x
where x.rank = 'preferred' or not x.has_preferred;

-- Labels ----------------------------------------------------------------------------------------------------
-- The label of an IRI, else the IRI. A label with a language other than English comes after an English or
-- untagged one.
create or replace function substrate.label(p_iri text) returns text language sql stable
set search_path = pg_catalog as $$
  select coalesce((select l.object_value ->> '@value' from substrate.usable l
                   where l.subject = p_iri and l.predicate = substrate.term('label') and l.object_value is not null
                   order by coalesce(l.object_value ->> '@language', 'en') = 'en' desc, l.recorded_at, l.id limit 1),
                  p_iri) $$;

create or replace function substrate.description(p_iri text) returns text language sql stable
set search_path = pg_catalog as $$
  select d.object_value ->> '@value' from substrate.usable d
  where d.subject = p_iri and d.predicate = substrate.term('description') and d.object_value is not null
  order by coalesce(d.object_value ->> '@language', 'en') = 'en' desc, d.recorded_at, d.id limit 1 $$;

create or replace function substrate.aliases(p_iri text) returns text[] language sql stable
set search_path = pg_catalog as $$
  select coalesce(array_agg(a.object_value ->> '@value' order by a.object_value ->> '@value'), '{}')
  from substrate.usable a
  where a.subject = p_iri and a.predicate = substrate.term('alt') and a.object_value is not null $$;

-- Items: what has a label in Substrate's namespaces.
create or replace view substrate.entity with (security_invoker = true) as
select s.iri, substrate.short(s.iri) as id, substrate.label(s.iri) as label,
       substrate.description(s.iri) as description, substrate.aliases(s.iri) as aliases
from (select distinct u.subject as iri from substrate.usable u
      where u.predicate = substrate.term('label') and substrate.in_namespace(u.subject)) s;

-- Formatting -----------------------------------------------------------------------------------------------------
-- A time as Substrate's page shows it. The claim keeps no precision: midnight shows as a day, and the 00 that
-- Substrate's own writer puts in an unknown month or day (precision 9 and 10) shows as a year or a month.
create or replace function substrate.fmt_time(p_time text) returns text language sql immutable parallel safe as $$
  select case when p_time ~ '^[0-9]+-00-00T00:00:00Z$' then split_part(p_time, '-', 1)
              when p_time ~ '^[0-9]+-[0-9]{2}-00T00:00:00Z$' then left(split_part(p_time, 'T', 1), -3)
              when p_time ~ 'T00:00:00Z$' then split_part(p_time, 'T', 1)
              else replace(rtrim(p_time, 'Z'), 'T', ' ') end $$;

create or replace function substrate.fmt_value(p_entity text, p_value jsonb) returns text language sql stable
set search_path = pg_catalog as $$
  select case
    when p_entity is not null then format('%s (%s)', substrate.label(p_entity), substrate.short(p_entity))
    when p_value ->> '@type' = 'http://www.w3.org/2001/XMLSchema#anyURI' then '<' || (p_value ->> '@value') || '>'
    when p_value ->> '@type' = 'http://www.w3.org/2001/XMLSchema#dateTime' then substrate.fmt_time(p_value ->> '@value')
    else p_value ->> '@value' end $$;

-- A qualifier value is an IRI or a plain text; it is an entity when something is said about it.
create or replace function substrate.fmt_qualifier(p_value text) returns text language sql stable
set search_path = pg_catalog as $$
  select case
    when p_value like 'urn:kk:%' or exists (select 1 from substrate.usable u
                                            where u.subject = p_value and u.predicate = substrate.term('label'))
      then format('%s (%s)', substrate.label(p_value), substrate.short(p_value))
    when p_value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' then substrate.fmt_time(p_value)
    else p_value end $$;

-- One reference as Substrate's page shows it. The encoder keeps the kind, name and version in the reference's
-- IRI, the span as source and locator; a parser's run, a rule's premises and a note are not kept.
create or replace function substrate.fmt_reference(p_uri text, p_quote text, p_span jsonb)
returns text language sql immutable parallel safe set search_path = pg_catalog as $$
  select concat_ws(' ',
    format('%s %s', m[1], substrate.urldecode(m[2])),
    case when m[3] is not null then format('(version %s)', substrate.urldecode(m[3])) end,
    case when p_span is not null then
      format('in %s at %s: "%s"', p_span ->> 'source', p_span ->> 'value',
             case when length(p_quote) > 160 then left(p_quote, 157) || '...' else p_quote end) end)
  from (select regexp_match(p_uri, '^urn:kk:substrate:reference:[^?]*\?by=([^:]*):([^&]*)(?:&version=(.*))?$') as m) x
  where m is not null $$;

-- Search -----------------------------------------------------------------------------------------------------
-- What a page says in words (label, description, aliases, each statement's property, value and qualifiers, its references), for full-text
-- search. Cheaper than rendering every page; the rank differs a little from Substrate's (see D-104).
create or replace view substrate.entity_text with (security_invoker = true) as
select e.iri,
       to_tsvector('simple', concat_ws(' ', e.label, e.description, array_to_string(e.aliases, ' '),
         (select string_agg(concat_ws(' ', substrate.label(u.predicate), substrate.short(u.predicate),
                                      case when u.object_entity is not null
                                           then substrate.label(u.object_entity) || ' ' || substrate.short(u.object_entity)
                                           else u.object_value ->> '@value' end, ' ',
                                      (select string_agg(substrate.label(k.key) || ' ' || substrate.fmt_qualifier(v.value), ' ')
                                       from jsonb_each(u.qualifiers) k, lateral substrate.qvals(u.qualifiers, k.key) v)), ' ')
          from substrate.usable u
          where u.subject = e.iri and u.predicate not in (substrate.term('label'), substrate.term('description'),
                                                          substrate.term('alt'))),
         (select string_agg(distinct substrate.fmt_reference(r.source_uri, r.quote, r.span), ' ')
          from substrate.usable u join api.claim_references r on r.claim_id = u.id
          where u.subject = e.iri and r.source_uri like 'urn:kk:substrate:reference:%'))) as tsv
from substrate.entity e;

-- Like store.search: the item with that id first, then by the best of label similarity, alias similarity and
-- full-text rank; found by label (trigram or substring), alias (substring) or the words of its page.
create or replace function substrate.search(p_query text, p_limit int default 10)
returns table (id text, iri text, label text, description text, score real)
language sql stable set search_path = pg_catalog as $$
  select s.id, s.iri, s.label, s.description, s.score from (
    select e.id, e.iri, e.label, e.description,
           greatest(substrate.sim(e.label, p_query),
                    coalesce((select max(substrate.sim(a, p_query)) from unnest(e.aliases) a), 0),
                    coalesce(ts_rank(t.tsv, websearch_to_tsquery('simple', p_query)), 0))::real as score,
           (e.id = upper(p_query) or e.iri = p_query) as exact,
           (substrate.sim(e.label, p_query) > 0.3 or e.label ilike '%' || p_query || '%'
            or exists (select 1 from unnest(e.aliases) a where a ilike '%' || p_query || '%')
            or t.tsv @@ websearch_to_tsquery('simple', p_query)) as hit
    from substrate.entity e join substrate.entity_text t on t.iri = e.iri
  ) s
  where s.exact or s.hit
  order by s.exact desc, s.score desc, s.id
  limit p_limit $$;

-- Pages -----------------------------------------------------------------------------------------------------
-- An item as Markdown, as store.render lays it out: its label and short id, description, aliases; its usable
-- statements (all ranks with p_all_ranks) grouped by property label, each value shown by label, rank and
-- numbered references after it, qualifiers under it; how many lower-ranked statements are not shown; the
-- references. Null when nothing is said about the IRI.
create or replace function substrate.page(p_iri text, p_all_ranks boolean default false) returns text
language plpgsql stable set search_path = pg_catalog as $$
declare
  out text; r record; q record; ref record; refs text[] := '{}'; n int; marks text; prev text;
  hidden int; d text; al text[]; labels text[] := array[substrate.term('label'), substrate.term('description'),
                                                         substrate.term('alt')];
begin
  if not exists (select 1 from api.claims c where c.subject = p_iri and c.redacted_at is null) then
    return null;
  end if;
  out := format(E'# %s (%s)\n', substrate.label(p_iri), substrate.short(p_iri));
  d := substrate.description(p_iri);
  if d is not null then out := out || E'\n' || d || E'\n'; end if;
  al := substrate.aliases(p_iri);
  if cardinality(al) > 0 then out := out || E'\nAlso known as: ' || array_to_string(al, ', ') || E'\n'; end if;
  for r in
    select c.id, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.status, c.recorded_at,
           substrate.label(c.predicate) as plabel
    from api.claims c
    where c.subject = p_iri and c.redacted_at is null and not (c.predicate = any (labels))
      and (case when p_all_ranks
                then c.status in ('normal', 'preferred')
                     or (c.status = 'deprecated' and not exists (        -- not a version the item has since restated
                           select 1 from api.claims k where k.subject = c.subject and k.statement_key = c.statement_key
                             and k.status in ('normal', 'preferred')))
                else c.id in (select u.id from substrate.usable u where u.subject = p_iri) end)
    order by plabel, c.predicate, c.recorded_at, c.id
  loop
    if prev is null then out := out || E'\n## Statements\n'; end if;
    if r.predicate is distinct from prev then
      out := out || format(E'\n**%s** (%s)\n', r.plabel, substrate.short(r.predicate));
      prev := r.predicate;
    end if;
    marks := '';
    for ref in select distinct x.source_uri from api.claim_references x
               where x.claim_id = r.id and x.source_uri like 'urn:kk:substrate:reference:%' order by 1 loop
      n := array_position(refs, ref.source_uri);
      if n is null then refs := refs || ref.source_uri; n := cardinality(refs); end if;
      marks := marks || format('[%s]', n);
    end loop;
    out := out || format(E'- %s%s%s\n',
                         replace(substrate.fmt_value(r.object_entity, r.object_value), E'\n', E'\n  '),
                         case when r.status <> 'normal' then format(' (%s)', r.status) else '' end,
                         case when marks <> '' then ' ' || marks else '' end);
    for q in select substrate.label(k.key) as qlabel, k.key as qprop, v.value, v.n
             from jsonb_each(r.qualifiers) k, lateral substrate.qvals(r.qualifiers, k.key) v
             order by 1, 2, 4 loop
      out := out || format(E'  - %s: %s\n', q.qlabel, replace(substrate.fmt_qualifier(q.value), E'\n', E'\n    '));
    end loop;
  end loop;
  if not p_all_ranks then
    select count(*) into hidden from api.claims c
    where c.subject = p_iri and c.redacted_at is null and not (c.predicate = any (labels))
      and c.status in ('normal', 'preferred', 'deprecated')
      and c.id not in (select u.id from substrate.usable u where u.subject = p_iri)
      and not (c.status = 'deprecated' and exists (
             select 1 from api.claims k where k.subject = c.subject and k.statement_key = c.statement_key
               and k.status in ('normal', 'preferred')));
    if hidden > 0 then
      out := out || format(E'\n%s statement(s) at a lower rank are not shown (read with all ranks to see them).\n', hidden);
    end if;
  end if;
  if cardinality(refs) > 0 then
    out := out || E'\n## References\n\n';
    for n in 1 .. cardinality(refs) loop
      select substrate.fmt_reference(x.source_uri, x.quote, x.span) into d from api.claim_references x
      where x.source_uri = refs[n] limit 1;
      out := out || format(E'%s. %s\n', n, d);
    end loop;
  end if;
  return out;
end $$;

-- Maps ----------------------------------------------------------------------------------------------------------
-- What could fill a role: everything that provides every interface the role requires (none required: none
-- found), with whether it fills the role now, and in which whole (IRIs, as Substrate has ids).
create or replace function substrate.alternatives(p_role text)
returns table (entity text, label text, fills_now boolean, in_whole text, provides text[])
language sql stable set search_path = pg_catalog as $$
  with need as (
    select u.object_entity as iface from substrate.usable u
    where u.subject = p_role and u.predicate = substrate.term('requires') and u.object_entity is not null
  ), cand as (
    select u.subject, array_agg(distinct u.object_entity order by u.object_entity) as provides
    from substrate.usable u where u.predicate = substrate.term('provides') and u.object_entity is not null
    group by u.subject
  ), filling as (
    select h.object_entity as part, h.subject as whole
    from substrate.usable h, lateral substrate.qvals(h.qualifiers, substrate.term('role')) q
    where h.predicate = substrate.term('has-part') and h.object_entity is not null and q.value = p_role
  )
  select c.subject, substrate.label(c.subject), exists (select 1 from filling f where f.part = c.subject),
         (select string_agg(f.whole, ', ' order by f.whole) from filling f where f.part = c.subject), c.provides
  from cand c
  where exists (select 1 from need) and (select array_agg(iface) from need) <@ c.provides
  order by 3 desc, 2 $$;

-- A whole, its parts to any depth with the role each plays, the interfaces roles require and parts provide, and
-- what else could fill each role: the columns and kinds of Substrate's store.map. `flagged` (a statement resting
-- on a changed span) has no source in the claims yet: it is false.
create or replace function substrate.map(p_whole text, p_depth int default 6)
returns table (source text, target text, kind text, statement uuid, flagged boolean)
language sql stable set search_path = pg_catalog as $$
  with recursive part (whole, part, st, quals, depth) as (
    select h.subject, h.object_entity, h.id, h.qualifiers, 1 from substrate.usable h
    where h.subject = p_whole and h.predicate = substrate.term('has-part') and h.object_entity is not null
    union
    select h.subject, h.object_entity, h.id, h.qualifiers, p.depth + 1
    from part p join substrate.usable h on h.subject = p.part
    where h.predicate = substrate.term('has-part') and h.object_entity is not null and p.depth < p_depth
  ), plays as (
    select p.part, q.value as role, p.st from part p, lateral substrate.qvals(p.quals, substrate.term('role')) q
  ), holder as (                                   -- everything that can provide an interface: the whole and its parts
    select p_whole as e union select part from part
  ), role as (select distinct role from plays),
  cand as (
    select a.entity, r.role from role r, lateral substrate.alternatives(r.role) a
    where a.entity not in (select e from holder)
  ), edge as (
    select whole, part, 'has-part', st from part
    union all select part, role, 'plays', null::uuid from plays
    union all select u.subject, u.object_entity, 'requires', u.id from substrate.usable u
      where u.predicate = substrate.term('requires') and u.object_entity is not null
        and u.subject in (select role from role)
    union all select u.subject, u.object_entity, 'provides', u.id from substrate.usable u
      where u.predicate = substrate.term('provides') and u.object_entity is not null
        and (u.subject in (select e from holder) or u.subject in (select entity from cand))
    union all select entity, role, 'could-fill', null::uuid from cand
  )
  select distinct e.*, false from edge e $$;

create or replace function substrate.map_nodes(p_whole text, p_depth int default 6)
returns table (id text, label text, description text, kind text)
language sql stable set search_path = pg_catalog as $$
  with m as (select * from substrate.map(p_whole, p_depth)),
  n as (select p_whole as id union select source from m union select target from m)
  select n.id, substrate.label(n.id), substrate.description(n.id),
         case when n.id = p_whole then 'whole'
              when exists (select 1 from m where m.target = n.id and m.kind in ('plays', 'could-fill')) then 'role'
              when exists (select 1 from m where m.target = n.id and m.kind in ('requires', 'provides')) then 'interface'
              when exists (select 1 from m where m.source = n.id and m.kind = 'could-fill') then 'candidate'
              else 'part' end
  from n order by 1 $$;

-- The wholes the UI lists: what has parts, the one with most first.
create or replace view substrate.wholes with (security_invoker = true) as
select u.subject as iri, substrate.short(u.subject) as id, substrate.label(u.subject) as label,
       substrate.description(u.subject) as description, count(*) as parts
from substrate.usable u
where u.predicate = substrate.term('has-part') and u.object_entity is not null
group by u.subject
order by count(*) desc, 3;

grant usage on schema substrate to kk_agent;
grant select on substrate.usable, substrate.entity, substrate.entity_text, substrate.wholes to kk_agent;
grant execute on all functions in schema substrate to kk_agent;
reset role;

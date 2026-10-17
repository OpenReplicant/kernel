-- Module install: recall-index (D-60). Runs once as a superuser; see modules/_template/migration.sql.
-- A derived, rebuildable index in its own schema `recall`, never a kernel table: one row per claim the
-- module can read (its text as a tsvector and its length) and BM25 statistics per scope. Rows carry the
-- claim's scope and the same row-level security as the kernel (kernel.can_read), so a reader finds only
-- claims, and term counts, of scopes they may read. Drop the schema and the worker rebuilds it.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_recall_index') then
    create role kkm_recall_index nologin;
  end if;
end $$;
grant kk_agent to kkm_recall_index;
grant kkm_recall_index to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:recall-index', 'kkm_recall_index', 'software', 'Recall index: BM25 over claims')
on conflict (iri) do nothing;
reset role;

create schema if not exists recall authorization kkm_recall_index;
set role kkm_recall_index;
create table if not exists recall.doc (
  claim_id uuid primary key,
  scope    text not null,
  len      int not null,
  tsv      tsvector not null
);
create index if not exists doc_tsv on recall.doc using gin (tsv);
create table if not exists recall.term (
  scope  text not null,
  lexeme text not null,
  df     int not null,
  primary key (scope, lexeme)
);
create table if not exists recall.corpus (
  scope     text primary key,
  n         int not null,
  total_len bigint not null
);
create table if not exists recall.state (
  id          boolean primary key default true check (id),
  recorded_at timestamptz,
  redacted_at timestamptz
);
alter table recall.doc enable row level security;
alter table recall.term enable row level security;
alter table recall.corpus enable row level security;
drop policy if exists readable on recall.doc;
drop policy if exists readable on recall.term;
drop policy if exists readable on recall.corpus;
create policy readable on recall.doc for select using (kernel.can_read(scope));
create policy readable on recall.term for select using (kernel.can_read(scope));
create policy readable on recall.corpus for select using (kernel.can_read(scope));
grant usage on schema recall to kk_agent;
grant select on recall.doc, recall.term, recall.corpus to kk_agent;
reset role;

-- BM25 (k1 1.2, b 0.75) over the index, as the caller: row-level security on recall.* and api.claims
-- applies, so only claims (and term statistics) of readable scopes count. Same row shape as the plain
-- full-text recall in kernel-mcp, plus `rank`. kernel-mcp uses this function when it exists.
set role kk_owner;
set check_function_bodies = off;   -- the body reads recall.*, which kk_owner need not read itself
create or replace function api.recall(p_text text, p_limit int) returns jsonb
language sql stable security invoker set search_path = pg_catalog as $$
  with q as (select array_agg(lexeme) as lx, to_tsquery('english', coalesce(string_agg(lexeme, ' | '), '')) as tsq
             from unnest(to_tsvector('english', p_text))),
  -- Statistics over every scope the caller may read (row-level security limits the sums to those),
  -- so scores from a small scope and a large one are comparable.
  k as (select sum(n)::float8 as n, greatest(sum(total_len)::float8 / nullif(sum(n), 0), 1) as avgdl from recall.corpus),
  t as (select lexeme, sum(df)::float8 as df from recall.term, q where lexeme = any(q.lx) group by lexeme),
  hits as (select d.claim_id, d.len, u.lexeme, coalesce(array_length(u.positions, 1), 1) as tf
           from recall.doc d, q, unnest(d.tsv) u
           where q.lx is not null and d.tsv @@ q.tsq and u.lexeme = any(q.lx)),
  scored as (select h.claim_id,
                    sum(ln(1 + (k.n - t.df + 0.5) / (t.df + 0.5)) * h.tf * 2.2 / (h.tf + 1.2 * (0.25 + 0.75 * h.len / k.avgdl))) as rank
             from hits h join t on t.lexeme = h.lexeme, k
             group by h.claim_id)
  select coalesce(jsonb_agg(to_jsonb(r) order by r.rank desc, r.belief desc nulls last), '[]'::jsonb) from (
    select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.scope, c.evidence_type,
           c.attributed_to, c.status, c.belief, c.valid_from, c.valid_to, round(s.rank::numeric, 3) as rank
    from scored s join api.claims c on c.id = s.claim_id
    where c.status <> 'deprecated'
    order by s.rank desc, c.belief desc nulls last
    limit greatest(1, least(p_limit, 200))) r
$$;
grant execute on function api.recall(text, int) to kk_agent;
reset check_function_bodies;
reset role;

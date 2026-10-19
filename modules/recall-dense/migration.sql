-- Module install: recall-dense (D-61). Runs once as a superuser; see modules/_template/migration.sql.
-- Dense recall beside BM25: an embedding per claim with a text value, in its own schema `recall_dense`
-- (derived and rebuildable, never a kernel table), with the kernel's row-level security by scope, and
-- api.recall_hybrid, which fuses these with the recall index's BM25. Needs pgvector: where the extension
-- is not available (plain Postgres), this migration installs nothing and recall stays BM25.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'kkm_recall_dense') then
    create role kkm_recall_dense nologin;
  end if;
end $$;
grant kk_agent to kkm_recall_dense;
grant kkm_recall_dense to kk_authenticator;

set role kk_owner;
insert into kernel.agent(iri, db_role, kind, label)
values ('urn:kk:agent:recall-dense', 'kkm_recall_dense', 'software', 'Recall: dense embeddings fused with BM25')
on conflict (iri) do nothing;
reset role;

do $migration$ begin
  if not exists (select 1 from pg_available_extensions where name = 'vector') then
    raise notice 'recall-dense: pgvector is not available; dense recall not installed (BM25 only)';
    return;
  end if;
  create extension if not exists vector;
  create schema if not exists recall_dense authorization kkm_recall_dense;
  set local role kkm_recall_dense;
  -- One model per index: the model is part of the index (the worker refuses another one).
  create table if not exists recall_dense.state (
    id          boolean primary key default true check (id),
    model       text not null,
    recorded_at timestamptz,
    redacted_at timestamptz
  );
  create table if not exists recall_dense.vec (
    claim_id  uuid primary key,
    scope     text not null,
    embedding vector(384) not null
  );
  create index if not exists vec_hnsw on recall_dense.vec using hnsw (embedding vector_cosine_ops);
  alter table recall_dense.vec enable row level security;
  alter table recall_dense.state enable row level security;
  drop policy if exists readable on recall_dense.vec;
  drop policy if exists readable on recall_dense.state;
  create policy readable on recall_dense.vec for select using (kernel.can_read(scope));
  create policy readable on recall_dense.state for select using (true);
  grant usage on schema recall_dense to kk_agent;
  grant select on recall_dense.vec, recall_dense.state to kk_agent;
  reset role;

  -- BM25 (api.recall, from the recall index) and cosine similarity, each over its top 200, normalised and
  -- averaged (a convex combination, alpha 0.5: Bruch et al. 2023). Cosine is min-max normalised; BM25 is
  -- divided by its best score, since its list holds only matching claims: min-max would give the weakest
  -- match 0, the same as no match, and with few matches drop a relevant claim below unrelated ones (D-89). A claim the dense worker
  -- has not embedded yet (a memory written a moment ago) counts its BM25 score for both halves: missing
  -- is not irrelevant (D-89). As the caller:
  -- row-level security applies to both lists. Same row shape as api.recall; `rank` is the fused score.
  set local role kk_owner;
  set local check_function_bodies = off;
  execute $fn$
  create or replace function api.recall_hybrid(p_text text, p_vec vector, p_limit int) returns jsonb
  language sql stable security invoker set search_path = pg_catalog, public set hnsw.ef_search = 200 as $$
    with b as (select (e->>'id')::uuid as id, (e->>'rank')::float8 as s from jsonb_array_elements(api.recall(p_text, 200)) e),
    d as (select v.claim_id as id, 1 - (v.embedding <=> p_vec) as s from recall_dense.vec v
          order by v.embedding <=> p_vec limit 200),
    nb as (select id, coalesce(s / nullif(max(s) over (), 0), 1) as n from b),
    nd as (select id, coalesce((s - min(s) over ()) / nullif(max(s) over () - min(s) over (), 0), 1) as n from d),
    f as (select id, 0.5 * coalesce(nb.n, 0)
                     + 0.5 * coalesce(nd.n, case when not exists (select 1 from recall_dense.vec v where v.claim_id = id)
                                                 then nb.n end, 0) as rank
          from nb full join nd using (id))
    select coalesce(jsonb_agg(to_jsonb(r) order by r.rank desc, r.belief desc nulls last), '[]'::jsonb) from (
      select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.scope, c.evidence_type,
             c.attributed_to, c.status, c.belief, c.valid_from, c.valid_to, round(f.rank::numeric, 3) as rank
      from f join api.claims c on c.id = f.id
      where c.status <> 'deprecated'
      order by f.rank desc, c.belief desc nulls last
      limit greatest(1, least(p_limit, 200))) r
  $$ $fn$;
  grant execute on function api.recall_hybrid(text, vector, int) to kk_agent;
  reset role;
end $migration$;

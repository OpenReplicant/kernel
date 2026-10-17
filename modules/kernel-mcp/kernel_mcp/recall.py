"""Ranked recall over claims, shared by the MCP `search` tool and the harness hooks.

With a recall-index module installed (it adds `api.recall(text, int)`, D-60), ranking is that module's
(BM25 over its index), fused with dense embeddings when the recall-dense module has an index and
fastembed is installed here (`api.recall_hybrid`, D-61); otherwise Postgres full-text search, no extension: each claim's text is its literal value plus the words of its
subject IRI; the query is any word of the input (OR of its lexemes), ranked by ts_rank_cd, then
belief. When nothing matches, a case-insensitive substring match on labels and subject IRIs is the
fallback (short inputs, partial words, identifiers). Runs under the caller's role, so RLS applies.
"""
from __future__ import annotations

import os
import sys

COLUMNS = ("id, subject, predicate, object_entity, object_value, qualifiers, scope, evidence_type, "
           "attributed_to, status, belief, valid_from, valid_to")
DOC = ("to_tsvector('english', coalesce(c.object_value->>'@value', '') || ' ' || "
       "regexp_replace(c.subject, '[^[:alnum:]]+', ' ', 'g'))")
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

RANKED = f"""
with q as (select to_tsquery('english', coalesce(string_agg(lexeme, ' | '), '')) as tsq
           from unnest(to_tsvector('english', %(text)s)))
select coalesce(jsonb_agg(to_jsonb(r) order by r.rank desc, r.belief desc nulls last), '[]'::jsonb) from (
  select {', '.join('c.' + col for col in COLUMNS.split(', '))}, round(ts_rank_cd({DOC}, q.tsq)::numeric, 3) as rank
  from api.claims c, q
  where c.status <> 'deprecated' and {DOC} @@ q.tsq
  order by rank desc, c.belief desc nulls last limit %(n)s) r"""

SUBSTRING = f"""
select coalesce(jsonb_agg(to_jsonb(r)), '[]'::jsonb) from (
  select {COLUMNS} from api.claims
  where status <> 'deprecated' and (
    (predicate = '{LABEL}' and object_value->>'@value' ilike %(pat)s)
    or (predicate in ('{LABEL}', '{TYPE}') and subject ilike %(pat)s))
  order by (predicate = '{LABEL}') desc, subject limit %(n)s) r"""


INSTALLED = "select to_regprocedure('api.recall(text,integer)') is not null"
READY = "select exists (select 1 from recall.corpus where n > 0)"   # only once INSTALLED: the index has rows
VIA_INDEX = "select api.recall(%(text)s, %(n)s)"


DENSE = ("select case when to_regclass('recall_dense.vec') is not null then "
         "(select model from recall_dense.state where exists (select 1 from recall_dense.vec)) end")
VIA_HYBRID = "select api.recall_hybrid(%(text)s, %(vec)s::vector, %(n)s)"
MODEL = "BAAI/bge-small-en-v1.5"   # 384 dimensions, MIT; the recall-dense module's index is built with it
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge's query instruction
_models: dict = {}


def embedder(model: str = MODEL, download: bool = False):
    """The embedding model (fastembed, ONNX on CPU), loaded once from KK_EMBED_CACHE (default
    ~/.cache/knowledge-kernel/fastembed). Only the recall-dense worker downloads it (download=True): a
    search never waits on the network. None when fastembed is not installed or the model is not there."""
    if _models.get(model) is None:
        try:
            from fastembed import TextEmbedding
            cache = os.environ.get("KK_EMBED_CACHE") or os.path.expanduser("~/.cache/knowledge-kernel/fastembed")
            _models[model] = TextEmbedding(model_name=model, cache_dir=cache, local_files_only=not download)
        except Exception as e:  # recall falls back to BM25; say why
            print(f"dense recall off: {model}: {str(e)[:200]}", file=sys.stderr)
            return None
    return _models[model]


def vector(text: str, model: str = MODEL) -> str | None:
    """The query's embedding as a pgvector literal, or None when there is no embedder."""
    m = embedder(model)
    if m is None:
        return None
    v = next(iter(m.embed([QUERY_PREFIX + text])))
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def indexed(conn) -> bool:
    """Whether the recall index is installed and has rows (a synchronous connection)."""
    return bool(conn.execute(INSTALLED).fetchone()[0] and conn.execute(READY).fetchone()[0])


def dense(conn) -> str | None:
    """The dense index's model when the recall-dense module is installed and has rows."""
    return conn.execute(DENSE).fetchone()[0]


def ranked(indexed: bool, vec: str | None = None) -> str:
    """The ranked-recall statement: BM25 fused with dense recall when there is a query vector, BM25 when
    the recall index is installed, else full-text search."""
    if indexed and vec:
        return VIA_HYBRID
    return VIA_INDEX if indexed else RANKED


def params(text: str, limit: int, vec: str | None = None) -> dict:
    return {"text": text, "n": max(1, min(limit, 200)), "vec": vec,
            "pat": "%" + text.replace("\\", "").replace("%", "").replace("_", r"\_") + "%"}


def plan(conn, text: str, limit: int) -> tuple[str, dict]:
    """The statement and parameters for ranked recall of `text` on a synchronous connection."""
    on = indexed(conn)
    model = dense(conn) if on else None
    vec = vector(text, model) if model else None
    return ranked(on, vec), params(text, limit, vec)


def short(iri: str, prefixes: dict[str, str]) -> str:
    for prefix, ns in prefixes.items():
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return iri


def render(claims: list[dict], prefixes: dict[str, str], header: str) -> str:
    """Claims as compact lines an agent can cite: subject, predicate, object, claim id, belief."""
    if not claims:
        return ""
    lines = [header]
    for c in claims:
        obj = short(c["object_entity"], prefixes) if c["object_entity"] else \
            '"' + str((c["object_value"] or {}).get("@value", c["object_value"]))[:200] + '"'
        belief = "unscored" if c["belief"] is None else f"{c['belief']:.2f}"
        lines.append(f"- {short(c['subject'], prefixes)} {short(c['predicate'], prefixes)} {obj}"
                     f"  [claim {c['id']}, belief {belief}]")
    return "\n".join(lines)

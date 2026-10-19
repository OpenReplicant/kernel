"""Ranked recall over claims, shared by the MCP `search` tool and the harness hooks.

With a recall-index module installed (it adds `api.recall(text, int)`, D-60), ranking is that module's
(BM25 over its index), fused with dense embeddings when the recall-dense module has an index and
fastembed is installed here (`api.recall_hybrid`, D-61), then re-ranked by a cross-encoder when its model
is in the local cache (`rerank`, D-66); otherwise Postgres full-text search, no extension: each claim's text is its literal value plus the words of its
subject IRI; the query is any word of the input (OR of its lexemes), ranked by ts_rank_cd, then
belief. When nothing matches, a case-insensitive substring match on labels and subject IRIs is the
fallback (short inputs, partial words, identifiers). Runs under the caller's role, so RLS applies.
"""
from __future__ import annotations

import os
import re
import sys

COLUMNS = ("id, subject, predicate, object_entity, object_value, qualifiers, scope, evidence_type, "
           "attributed_to, status, belief, valid_from, valid_to")
# A claim's searchable text: its literal value, the words of its subject and object IRIs, and its
# predicate's name split at case changes (`softwareRequirements` -> "software Requirements"), so claims
# between entities (a package and what it requires) are found too (D-69). The recall index uses it too.
DOC = ("to_tsvector('english', coalesce(c.object_value->>'@value', '') || ' ' || "
       "regexp_replace(c.subject, '[^[:alnum:]]+', ' ', 'g') || ' ' || "
       "regexp_replace(coalesce(c.object_entity, ''), '[^[:alnum:]]+', ' ', 'g') || ' ' || "
       "regexp_replace(regexp_replace(c.predicate, '^.*[/#:]', ''), '([a-z])([A-Z])', '\\1 \\2', 'g'))")
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
            _models[model] = TextEmbedding(model_name=model, cache_dir=cache(), local_files_only=not download)
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


RERANKER = "jinaai/jina-reranker-v1-turbo-en"   # Apache-2.0; the best of five on SciFact (D-66)
POOL = 60   # claims the cross-encoder re-scores (about 30 papers), however few are asked for


def cache() -> str:
    return os.environ.get("KK_EMBED_CACHE") or os.path.expanduser("~/.cache/knowledge-kernel/fastembed")


def reranker(model: str = RERANKER, download: bool = False):
    """The cross-encoder (fastembed, ONNX on CPU), loaded once from the same cache as the embedder. The
    recall-dense worker downloads it when started with KK_RERANK=1; a search only uses a model that is
    already there, and KK_RERANK=0 turns it off. None when off, not installed or not downloaded."""
    if os.environ.get("KK_RERANK") == "0":
        return None
    key = "rerank:" + model
    if key not in _models:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            _models[key] = TextCrossEncoder(model_name=model, cache_dir=cache(), local_files_only=not download)
        except Exception as e:  # recall keeps the fused order; say why once
            print(f"re-ranking off: {model}: {str(e)[:200]}", file=sys.stderr)
            _models[key] = None
    return _models[key]


def pool(limit: int) -> int:
    """How many claims to recall for `limit` answers: POOL when there is a cross-encoder to re-rank them."""
    return max(limit, POOL) if reranker() else limit


def words(iri: str | None) -> str:
    """An IRI's last segment as words: `pkg:pypi/psycopg` -> "psycopg", `softwareRequirements` -> "software Requirements"."""
    local = re.split(r"[/#]", iri or "")[-1]
    return " ".join(re.split(r"[^0-9A-Za-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", local))).strip()


def literal(c: dict) -> str | None:
    value = (c.get("object_value") or {}).get("@value") if isinstance(c.get("object_value"), dict) else None
    return str(value) if value else None


def passage(c: dict) -> str:
    """What the cross-encoder reads of a claim: its literal value, else its subject, predicate, object and
    qualifiers as words ("knowledge kernel software Requirements psycopg version ==3.3.6")."""
    return literal(c) or " ".join(filter(None, [words(c.get("subject")), words(c.get("predicate")),
                                                words(c.get("object_entity")),
                                                *(f"{words(k)} {v}" for k, v in (c.get("qualifiers") or {}).items())]))


TEXTS = """select coalesce(jsonb_object_agg(subject, text), '{}'::jsonb) from (
  select subject, left(string_agg(object_value->>'@value', E'\n' order by length(object_value->>'@value'), id), 3000) as text
  from api.claims where subject = any(%(subjects)s) and status <> 'deprecated' and object_value ? '@value'
  group by subject) t"""


def subjects(claims: list[dict]) -> list[str]:
    """The distinct subjects of the first POOL claims, in recalled order: what the cross-encoder scores."""
    return list(dict.fromkeys(c.get("subject") or "" for c in claims[:POOL]))


def rerank(text: str, claims: list[dict], limit: int, texts: dict[str, str] | None = None) -> list[dict]:
    """The first `limit` claims, re-ordered by the cross-encoder when there is one. It reads per subject:
    the subject's text (`texts`, from TEXTS: all its literal claims, shortest first, so a paper's title then
    its abstract; else its recalled claims' passages) scored against `text`. Subjects go in score order, each
    with its recalled claims; the claims after the first POOL keep their place (the cost stays bounded)."""
    ce = reranker()
    if ce is None or len(claims) < 2:
        return claims[:limit]
    head, tail = claims[:POOL], claims[POOL:]
    groups: dict[str, list[dict]] = {}
    for c in head:
        groups.setdefault(c.get("subject") or "", []).append(c)
    keys = list(groups)
    # The subject's text claims (TEXTS) and, written out, its recalled claims between entities, which
    # TEXTS cannot hold (a package's requirements); without TEXTS, every recalled claim's passage.
    docs = ["\n".join(dict.fromkeys(filter(None, [(texts or {}).get(k), *(passage(c) for c in groups[k]
                                                                          if not (texts and literal(c)))])))[:3000]
            for k in keys]
    scores = list(ce.rerank(text, docs))
    order = sorted(range(len(keys)), key=lambda i: -scores[i])
    return ([c for i in order for c in groups[keys[i]]] + tail)[:limit]


def indexed(conn) -> bool:
    """Whether the recall index is installed and has rows (a synchronous connection)."""
    return bool(conn.execute(INSTALLED).fetchone()[0] and conn.execute(READY).fetchone()[0])


def dense_on() -> bool:
    """KK_DENSE=0 turns dense recall off (BM25 only), as KK_RERANK=0 turns re-ranking off."""
    return os.environ.get("KK_DENSE") != "0"


def dense(conn) -> str | None:
    """The dense index's model when the recall-dense module is installed and has rows (and dense is on)."""
    return conn.execute(DENSE).fetchone()[0] if dense_on() else None


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
    """The statement and parameters for ranked recall of `text` on a synchronous connection (it recalls
    `pool(limit)` claims: pass them through `rerank`)."""
    on = indexed(conn)
    model = dense(conn) if on else None
    vec = vector(text, model) if model else None
    return ranked(on, vec), params(text, pool(limit) if on else limit, vec)


def recall(conn, text: str, limit: int) -> list[dict]:
    """Ranked recall of `text` on a synchronous connection: the plan, re-ranked."""
    rows = conn.execute(*plan(conn, text, limit)).fetchone()[0]
    if not (indexed(conn) and reranker()):
        return rows[:limit]
    return rerank(text, rows, limit, conn.execute(TEXTS, {"subjects": subjects(rows)}).fetchone()[0])


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
        quals = "; ".join(f"{re.split(r'[/#:]', k)[-1]} {v}" for k, v in (c.get("qualifiers") or {}).items())
        lines.append(f"- {short(c['subject'], prefixes)} {short(c['predicate'], prefixes)} {obj}"
                     + (f" ({quals})" if quals else "") + f"  [claim {c['id']}, belief {belief}]")
    return "\n".join(lines)

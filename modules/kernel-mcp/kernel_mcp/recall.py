"""Ranked recall over claims, shared by the MCP `search` tool and the harness hooks.

With a recall-index module installed (it adds `api.recall(text, int)`, D-60), ranking is that module's
(BM25 over its index); otherwise Postgres full-text search, no extension: each claim's text is its literal value plus the words of its
subject IRI; the query is any word of the input (OR of its lexemes), ranked by ts_rank_cd, then
belief. When nothing matches, a case-insensitive substring match on labels and subject IRIs is the
fallback (short inputs, partial words, identifiers). Runs under the caller's role, so RLS applies.
"""
from __future__ import annotations

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


def indexed(conn) -> bool:
    """Whether the recall index is installed and has rows (a synchronous connection)."""
    return bool(conn.execute(INSTALLED).fetchone()[0] and conn.execute(READY).fetchone()[0])


def ranked(indexed: bool) -> str:
    """The ranked-recall statement: the recall-index module's when installed."""
    return VIA_INDEX if indexed else RANKED


def params(text: str, limit: int) -> dict:
    return {"text": text, "n": max(1, min(limit, 200)),
            "pat": "%" + text.replace("\\", "").replace("%", "").replace("_", r"\_") + "%"}


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

"""Agent-facing MCP tools over the kernel's api schema, on the MCP Python SDK v2 (MCPServer).

Talks to Postgres directly. Every tool call runs in its own transaction as the caller's database
role, so row-level security, write clearance and attribution are enforced by Postgres per agent;
this server decides nothing. Who the caller is:
  - stdio (one harness, one agent): DATABASE_URL is the agent's own login (e.g. agent_claude_code).
    The server never switches roles in this mode, and refuses a login that could (kk_authenticator,
    superusers): whoever holds such a password could act as anyone, the owner included.
  - http (shared server): DATABASE_URL is kk_authenticator, which only this server holds; each
    request's bearer JWT (signed with KK_JWT_SECRET, `role` claim, the same tokens PostgREST accepts)
    names the role it switches to (SET LOCAL ROLE). A request without a valid token is refused.
Every claim returned carries its id and belief, so answers can cite them (invariant 6).
"""
from __future__ import annotations

import os
from typing import Any

import jwt
import psycopg
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql
from psycopg.types.json import Jsonb

PREFIXES = {  # same as kernel.prefix (kernel/schema/002_bootstrap.sql)
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#", "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#", "dcterms": "http://purl.org/dc/terms/",
    "prov": "http://www.w3.org/ns/prov#", "pplan": "http://purl.org/net/p-plan#", "oa": "http://www.w3.org/ns/oa#",
    "sh": "http://www.w3.org/ns/shacl#", "td": "https://www.w3.org/2019/wot/td#",
    "kk": "https://w3id.org/knowledge-kernel/ns#", "kkt": "https://w3id.org/knowledge-kernel/tosca#",
    "kkp": "https://w3id.org/knowledge-kernel/profile#",
}
CLAIM_COLUMNS = ("id, subject, predicate, object_entity, object_value, qualifiers, scope, evidence_type, "
                 "attributed_to, status, belief, valid_from, valid_to")
RDF_TYPE = PREFIXES["rdf"] + "type"
LABEL = PREFIXES["rdfs"] + "label"
REQUIRE_TOKEN = False  # set by the http entrypoint

mcp = MCPServer(
    "knowledge-kernel",
    instructions="Knowledge Kernel tools. Read claims (each has an id and a belief) and submit change sets "
                 "under your own identity. Cite claim ids and their belief in every answer. "
                 "See the knowledge-kernel skill for the write rules.",
)


class KernelError(ToolError):
    """An anticipated failure (no identity, the kernel refused): the agent sees the message."""


def expand(term: str | None) -> str | None:
    if term is None:
        return None
    prefix, sep, local = term.partition(":")
    if sep and prefix in PREFIXES and not local.startswith("//"):
        return PREFIXES[prefix] + local
    return term


def caller_role(ctx: Context) -> str | None:
    """The role to switch to (http), or None when the login itself is the caller (stdio)."""
    if not REQUIRE_TOKEN:
        return None
    auth = (ctx.headers or {}).get("authorization", "")
    if not auth.lower().startswith("bearer ") or not auth[7:].strip():
        raise KernelError("no bearer token: send 'Authorization: Bearer <your agent JWT>'")
    try:
        claims = jwt.decode(auth[7:].strip(), os.environ["KK_JWT_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise KernelError(f"invalid token: {e}") from None
    if not claims.get("role"):
        raise KernelError("token has no role claim")
    return claims["role"]


SWITCHING_LOGIN = """select rolsuper or rolname = 'kk_authenticator' from pg_roles where rolname = current_user"""


async def query(ctx: Context, statement: str, params: tuple | dict = ()) -> Any:
    """Run one statement as the caller; returns the first column of the first row."""
    role = caller_role(ctx)
    try:
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"]) as conn:
            async with conn.transaction():
                if role:
                    await conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
                elif (await (await conn.execute(SWITCHING_LOGIN)).fetchone())[0]:
                    raise KernelError("stdio needs the agent's own login in DATABASE_URL, not one that can "
                                      "switch roles (kk_authenticator or a superuser)")
                cur = await conn.execute(statement, params)
                row = await cur.fetchone()
                return row[0] if row else None
    except psycopg.Error as e:
        raise KernelError(f"kernel refused: {(e.diag.message_primary or str(e))[:500]}") from None


def rows(select: str) -> str:
    """Wrap a select so Postgres returns its rows as one JSON array."""
    return f"select coalesce(jsonb_agg(to_jsonb(r)), '[]'::jsonb) from ({select}) r"


# ------------------------------------------------------------------ reading
@mcp.tool()
async def query_claims(ctx: Context, subject: str | None = None, predicate: str | None = None,
                       object_entity: str | None = None, scope: str | None = None,
                       include_deprecated: bool = False, limit: int = 100) -> list[dict]:
    """Claims matching all given filters (IRIs or CURIEs such as rdfs:label). Each has id, status and belief."""
    return await query(ctx, rows(
        f"select {CLAIM_COLUMNS} from api.claims"
        " where (%(s)s::text is null or subject = %(s)s) and (%(p)s::text is null or predicate = %(p)s)"
        "   and (%(o)s::text is null or object_entity = %(o)s) and (%(sc)s::text is null or scope = %(sc)s)"
        "   and (%(dep)s or status <> 'deprecated')"
        " order by subject, predicate limit %(n)s"),
        {"s": expand(subject), "p": expand(predicate), "o": expand(object_entity), "sc": expand(scope),
         "dep": include_deprecated, "n": max(1, min(limit, 1000))})


@mcp.tool()
async def get_entity(ctx: Context, iri: str) -> dict:
    """The entity card: current claims about the entity, and claims that point at it."""
    iri = expand(iri)
    return await query(ctx, f"""select jsonb_build_object('iri', %(i)s::text,
        'claims', ({rows(f"select {CLAIM_COLUMNS} from api.claims where subject = %(i)s and status <> 'deprecated' order by predicate limit 500")}),
        'referenced_by', ({rows(f"select {CLAIM_COLUMNS} from api.claims where object_entity = %(i)s and status <> 'deprecated' order by predicate limit 200")}))""",
        {"i": iri})


@mcp.tool()
async def search(ctx: Context, text: str, limit: int = 20) -> list[dict]:
    """Claims whose subject IRI or label contains the text (case-insensitive): labels and types first."""
    return await query(ctx, rows(
        f"select {CLAIM_COLUMNS} from api.claims"
        " where status <> 'deprecated' and ("
        "   (predicate = %(label)s and object_value->>'@value' ilike %(pat)s)"
        "   or (predicate in (%(label)s, %(type)s) and subject ilike %(pat)s))"
        " order by (predicate = %(label)s) desc, subject limit %(n)s"),
        {"label": LABEL, "type": RDF_TYPE, "pat": "%" + text.replace("%", "").replace("_", r"\_") + "%",
         "n": max(1, min(limit, 200))})


@mcp.tool()
async def explain(ctx: Context, claim_id: str) -> dict:
    """Where a claim came from: sources and quotes, derivations, uses, status history, beliefs, verifications, run."""
    return await query(ctx, "select api.explain(%s::uuid)", (claim_id,))


async def _typed(ctx: Context, cls: str, props: list[str]) -> list[dict]:
    return await query(ctx, f"""
        select coalesce(jsonb_agg(jsonb_build_object(
                 'iri', t.subject, 'type_claim', jsonb_build_object('id', t.id, 'belief', t.belief),
                 'claims', ({rows(f"select {CLAIM_COLUMNS} from api.claims f where f.subject = t.subject and f.predicate = any(%(props)s) and f.status <> 'deprecated'")}))
               order by t.subject), '[]'::jsonb)
        from api.claims t where t.predicate = %(type)s and t.object_entity = %(cls)s and t.status <> 'deprecated'""",
        {"type": RDF_TYPE, "cls": expand(cls), "props": [expand(p) for p in props]})


@mcp.tool()
async def list_slots(ctx: Context) -> list[dict]:
    """The slot catalog as the kernel knows it: each slot with its contract and cardinality claims."""
    return await _typed(ctx, "kk:Slot", ["rdfs:label", "kk:slotContract", "kk:cardinality"])


@mcp.tool()
async def list_modules(ctx: Context) -> list[dict]:
    """Modules in the self-model: label, version, kind, the slots they fill and the agent they run as."""
    return await _typed(ctx, "kk:Module", ["rdfs:label", "kk:version", "kk:moduleKind", "kk:fills", "kk:runsAs",
                                           "kk:protected"])


# ------------------------------------------------------------------ writing
@mcp.tool()
async def start_run(ctx: Context, module: str | None = None, plan_step: str | None = None,
                    info: dict | None = None) -> str:
    """Start a run under your own identity; every change set names the run that produced it."""
    return str(await query(ctx, "select api.start_run(%s, %s, '[]'::jsonb, %s)",
                           (module, plan_step, Jsonb(info or {}))))


@mcp.tool()
async def submit_change_set(ctx: Context, change_set: dict) -> dict:
    """Submit a change set (kernel/schemas/change_set.schema.json). Returns {id, status, lane}.
    attributed_to must be your own agent IRI and generated_by one of your running runs."""
    return await query(ctx, "select api.submit_change_set(%s)", (Jsonb(change_set),))


@mcp.tool()
async def get_change_set(ctx: Context, change_set_id: str) -> dict:
    """Status of one of your change sets, with the validation report when it is invalid."""
    found = await query(ctx, "select to_jsonb(c) from api.change_sets c where id = %s::uuid", (change_set_id,))
    if not found:
        raise KernelError(f"change set {change_set_id} not found among those you can see")
    return found


@mcp.tool()
async def finish_run(ctx: Context, run_id: str, status: str = "succeeded", cost: dict | None = None) -> str:
    """Finish one of your runs: status succeeded or failed, cost e.g. {"tokens": 1200, "ms": 5300}."""
    await query(ctx, "select api.finish_run(%s::uuid, %s, %s)", (run_id, status, Jsonb(cost or {})))
    return status

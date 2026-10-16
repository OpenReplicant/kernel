"""Agent-facing MCP tools over PostgREST, on the MCP Python SDK v2 (MCPServer).

kernel-mcp holds no database role (D-20). Every tool forwards the caller's own bearer token, so
PostgREST runs the call as the caller's role: row-level security, write clearance and attribution
are enforced by Postgres per agent. A request without a token is refused here, before any call.
Every claim returned carries its id and belief, so answers can cite them (invariant 6).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

API = os.environ.get("KK_API", "http://postgrest:3000").rstrip("/")
PREFIXES = {  # same as kernel.prefix (kernel/schema/002_bootstrap.sql)
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#", "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#", "dcterms": "http://purl.org/dc/terms/",
    "prov": "http://www.w3.org/ns/prov#", "pplan": "http://purl.org/net/p-plan#", "oa": "http://www.w3.org/ns/oa#",
    "sh": "http://www.w3.org/ns/shacl#", "td": "https://www.w3.org/2019/wot/td#",
    "kk": "https://w3id.org/knowledge-kernel/ns#", "kkt": "https://w3id.org/knowledge-kernel/tosca#",
    "kkp": "https://w3id.org/knowledge-kernel/profile#",
}
CLAIM_COLUMNS = "id,subject,predicate,object_entity,object_value,qualifiers,scope,evidence_type,attributed_to,status,belief,valid_from,valid_to"
RDF_TYPE = PREFIXES["rdf"] + "type"

mcp = MCPServer(
    "knowledge-kernel",
    instructions="Knowledge Kernel tools. Read claims (each has an id and a belief) and submit change sets "
                 "under your own identity. Cite claim ids and their belief in every answer. "
                 "See the knowledge-kernel skill for the write rules.",
)


class KernelError(ToolError):
    """An anticipated failure (no token, the kernel refused): the agent sees the message."""


def expand(term: str) -> str:
    prefix, sep, local = term.partition(":")
    if sep and prefix in PREFIXES and not local.startswith("//"):
        return PREFIXES[prefix] + local
    return term


def token(ctx: Context) -> str:
    auth = (ctx.headers or {}).get("authorization", "")
    if not auth.lower().startswith("bearer ") or not auth[7:].strip():
        raise KernelError("no bearer token: send 'Authorization: Bearer <your agent JWT>'")
    return auth[7:].strip()


async def call(ctx: Context, method: str, path: str, params: dict | None = None, body: Any = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.request(method, f"{API}{path}", params=params, json=body,
                               headers={"Authorization": f"Bearer {token(ctx)}", "Accept": "application/json"})
    if r.status_code >= 300:
        raise KernelError(f"kernel refused ({r.status_code}): {r.text[:500]}")
    return r.json() if r.content else None


def quoted(values) -> str:
    return "(" + ",".join('"' + v.replace('"', "") + '"' for v in values) + ")"


# ------------------------------------------------------------------ reading
@mcp.tool()
async def query_claims(ctx: Context, subject: str | None = None, predicate: str | None = None,
                       object_entity: str | None = None, scope: str | None = None,
                       include_deprecated: bool = False, limit: int = 100) -> list[dict]:
    """Claims matching all given filters (IRIs or CURIEs such as kk:fills). Each has id, status and belief."""
    params = {"select": CLAIM_COLUMNS, "order": "subject,predicate", "limit": str(min(limit, 1000))}
    for key, value in (("subject", subject), ("predicate", predicate), ("object_entity", object_entity),
                       ("scope", scope)):
        if value:
            params[key] = f"eq.{expand(value)}"
    if not include_deprecated:
        params["status"] = "neq.deprecated"
    return await call(ctx, "GET", "/claims", params)


@mcp.tool()
async def get_entity(ctx: Context, iri: str) -> dict:
    """The entity card: current claims about the entity, and claims that point at it."""
    iri = expand(iri)
    about = await call(ctx, "GET", "/claims", {"select": CLAIM_COLUMNS, "subject": f"eq.{iri}",
                                               "status": "neq.deprecated", "order": "predicate", "limit": "500"})
    pointing = await call(ctx, "GET", "/claims", {"select": CLAIM_COLUMNS, "object_entity": f"eq.{iri}",
                                                  "status": "neq.deprecated", "order": "predicate", "limit": "200"})
    return {"iri": iri, "claims": about, "referenced_by": pointing}


@mcp.tool()
async def search(ctx: Context, text: str, limit: int = 20) -> list[dict]:
    """Entities whose IRI or label contains the text (case-insensitive), with the matching claims."""
    pattern = "*" + text.replace("*", "").replace(",", " ").replace("(", " ").replace(")", " ") + "*"
    by_iri = await call(ctx, "GET", "/claims", {"select": CLAIM_COLUMNS, "subject": f"ilike.{pattern}",
                                                "predicate": f"in.{quoted([RDF_TYPE, PREFIXES['rdfs'] + 'label'])}",
                                                "status": "neq.deprecated", "limit": str(limit)})
    by_label = await call(ctx, "GET", "/claims", {"select": CLAIM_COLUMNS, "predicate": f"eq.{PREFIXES['rdfs']}label",
                                                  "object_value->>@value": f"ilike.{pattern}",
                                                  "status": "neq.deprecated", "limit": str(limit)})
    seen, out = set(), []
    for row in by_label + by_iri:
        if row["id"] not in seen:
            seen.add(row["id"])
            out.append(row)
    return out[:limit]


@mcp.tool()
async def explain(ctx: Context, claim_id: str) -> dict:
    """Where a claim came from: sources and quotes, derivations, uses, status history, beliefs, verifications, run."""
    return await call(ctx, "GET", "/rpc/explain", {"claim_id": claim_id})


async def _typed(ctx: Context, cls: str, props: list[str]) -> list[dict]:
    typed = await call(ctx, "GET", "/claims", {"select": "id,subject,belief", "predicate": f"eq.{RDF_TYPE}",
                                               "object_entity": f"eq.{expand(cls)}", "status": "neq.deprecated",
                                               "order": "subject", "limit": "1000"})
    if not typed:
        return []
    facts = await call(ctx, "GET", "/claims", {
        "select": CLAIM_COLUMNS, "subject": f"in.{quoted(r['subject'] for r in typed)}",
        "predicate": f"in.{quoted(expand(p) for p in props)}", "status": "neq.deprecated", "limit": "5000"})
    out = {r["subject"]: {"iri": r["subject"], "type_claim": {"id": r["id"], "belief": r["belief"]}, "claims": []}
           for r in typed}
    for f in facts:
        out[f["subject"]]["claims"].append(f)
    return list(out.values())


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
    return await call(ctx, "POST", "/rpc/start_run", body={"module": module, "plan_step": plan_step,
                                                           "info": info or {}})


@mcp.tool()
async def submit_change_set(ctx: Context, change_set: dict) -> dict:
    """Submit a change set (kernel/schemas/change_set.schema.json). Returns {id, status, lane}.
    attributed_to must be your own agent IRI and generated_by one of your running runs."""
    return await call(ctx, "POST", "/rpc/submit_change_set", body={"cs": change_set})


@mcp.tool()
async def get_change_set(ctx: Context, change_set_id: str) -> dict:
    """Status of one of your change sets, with the validation report when it is invalid."""
    rows = await call(ctx, "GET", "/change_sets", {"id": f"eq.{change_set_id}"})
    if not rows:
        raise KernelError(f"change set {change_set_id} not found among those you can see")
    return rows[0]


@mcp.tool()
async def finish_run(ctx: Context, run_id: str, status: str = "succeeded", cost: dict | None = None) -> str:
    """Finish one of your runs: status succeeded or failed, cost e.g. {"tokens": 1200, "ms": 5300}."""
    await call(ctx, "POST", "/rpc/finish_run", body={"run_id": run_id, "status": status, "cost": cost or {}})
    return status

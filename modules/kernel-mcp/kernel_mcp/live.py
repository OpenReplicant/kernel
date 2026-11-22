"""`applied_since`: the applied order as a cursor, for views that follow the kernel live (D-116).

Reads `api.applied` (D-109) and `api.touched` (D-112) as the caller, so row-level security applies. Without a
cursor it answers the present one and nothing else; a view draws what is there, then calls again with the cursor.
"""
from __future__ import annotations

import psycopg
from mcp.server.mcpserver import Context

from .server import connection, mcp, one, rows


@mcp.tool()
async def applied_since(ctx: Context, cursor: int | None = None, limit: int = 200) -> dict:
    """Change sets applied after `cursor` (the `cursor` a previous call returned; none: the present one, no
    history), in the order they were applied, each with the claims it touched (claim_id, kind added | status |
    belief | verification, subject, object_entity, scope). `awaiting`: the ids of change sets waiting for
    review (people who may review only; empty for anyone else)."""
    async with connection(ctx) as conn:
        now = await one(conn, "select coalesce(max(seq), 0) from api.applied")
        if cursor is None:
            applied = []
        else:
            applied = await one(conn, rows(
                "select a.seq, a.change_set, a.applied_at, (select coalesce(jsonb_agg(distinct jsonb_build_object("
                "'claim_id', t.claim_id, 'kind', t.kind, 'subject', t.subject, 'object_entity', t.object_entity,"
                " 'scope', t.scope)), '[]'::jsonb) from api.touched t where t.change_set = a.change_set) as touched"
                " from api.applied a where a.seq > %(c)s order by a.seq limit %(n)s"),
                {"c": cursor, "n": max(1, min(limit, 1000))})
        try:
            async with conn.transaction():   # a savepoint: a refusal must not abort the caller's transaction
                awaiting = await one(conn, "select coalesce(jsonb_agg(id order by submitted_at), '[]'::jsonb)"
                                           " from api.review_queue")
        except psycopg.Error:   # not a reviewer: the view is not granted
            awaiting = []
    return {"cursor": applied[-1]["seq"] if applied else (cursor if cursor is not None else now),
            "applied": applied, "awaiting": awaiting}

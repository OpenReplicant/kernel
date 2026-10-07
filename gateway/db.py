"""Postgres access for the gateway: two pools, one per role, and calls into kernel functions.

The writer pool connects with a role that may only execute kernel.write,
kernel.ingest_source and kernel.cite. The reader pool connects with a role that may
only select. This module issues no INSERT, UPDATE or DELETE: every change goes through
the three kernel write functions.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.adapt import Dumper
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from gateway import otel
from gateway.cypher import dollar_tag
from gateway.problems import KernelRejection

REJECTION_SQLSTATE = "WMK01"
GRAPH = "world"


class Agtype(str):
    """A value sent to Postgres as agtype, so AGE sees a typed parameter."""


class Kernel:
    def __init__(self, writer_dsn: str, reader_dsn: str, *, max_size: int = 5) -> None:
        # Each connection is checked before it is lent: a database restart ends the ones a pool
        # holds, and the next call gets a new connection instead of failing as unreachable.
        check = AsyncConnectionPool.check_connection
        self.writer = AsyncConnectionPool(writer_dsn, min_size=1, max_size=max_size, open=False, check=check)
        self.reader = AsyncConnectionPool(
            reader_dsn, min_size=1, max_size=max_size, open=False, configure=_configure_reader, check=check
        )

    async def open(self) -> None:
        await self.writer.open(wait=True)
        await self.reader.open(wait=True)

    async def close(self) -> None:
        await self.writer.close()
        await self.reader.close()

    # Writes ------------------------------------------------------------------------

    async def write(self, payload: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
        return await self._call_writer("SELECT kernel.write(%s, %s)", [Jsonb(payload), agent_id])

    async def ingest_source(self, source: dict[str, Any], agent_id: str) -> dict[str, Any]:
        return await self._call_writer("SELECT kernel.ingest_source(%s, %s)", [Jsonb(source), agent_id])

    async def cite(self, cite: dict[str, Any], agent_id: str) -> dict[str, Any]:
        return await self._call_writer("SELECT kernel.cite(%s, %s)", [Jsonb(cite), agent_id])

    async def _call_writer(self, sql: str, params: list[Any]) -> dict[str, Any]:
        async with self.writer.connection() as conn:
            try:
                cur = await conn.execute(sql, params)
                row = await cur.fetchone()
            except psycopg.Error as exc:
                if exc.sqlstate == REJECTION_SQLSTATE and exc.diag.message_detail:
                    raise KernelRejection(json.loads(exc.diag.message_detail)) from None
                raise
        assert row is not None
        return row[0]

    # Reads -------------------------------------------------------------------------

    async def read_value(self, function: str, sql: str, params: list[Any]) -> Any:
        with otel.span(otel.SPAN_KERNEL_READ, **{otel.ATTR_READ: function}):
            async with self.reader.connection() as conn, conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                cur = await conn.execute(sql, params)
                row = await cur.fetchone()
                return row[0] if row else None

    async def read_rows(self, function: str, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        with otel.span(otel.SPAN_KERNEL_READ, **{otel.ATTR_READ: function}) as span:
            async with self.reader.connection() as conn, conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                cur = await conn.execute(sql, params)
                names = [d.name for d in cur.description or []]
                rows = [dict(zip(names, r, strict=True)) for r in await cur.fetchall()]
                otel.set_attributes(span, **{otel.ATTR_ROWS: len(rows)})
                return rows

    async def head_offset(self) -> int:
        return int(await self.read_value("head_offset", "SELECT kernel.head_offset()", []))

    async def resolve_candidates(
        self,
        name: str,
        node_type: str | None,
        kind: str | None,
        identity: dict[str, str] | None,
        embedding: list[float] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.read_rows(
            "resolve_candidates",
            "SELECT node_id, name, type, kind, namespace, stage, score::float AS score, band "
            "FROM kernel.resolve_candidates(%s, %s, %s, %s, %s::vector, %s)",
            [name, node_type, kind, Jsonb(identity or {}), _vector(embedding), limit],
        )

    async def schema_slice(self, passage: str, namespaces: list[str] | None, limit: int) -> dict[str, Any]:
        return await self.read_value(
            "schema_slice", "SELECT kernel.schema_slice(%s, %s, %s)", [passage, namespaces, limit]
        )

    async def query_log(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self.read_value("query_log", "SELECT kernel.query_log(%s)", [Jsonb(filters)])

    async def state_as_of(self, node_ids: list[str], edge_ids: list[str], offset: int) -> dict[str, Any]:
        return await self.read_value(
            "state_as_of", "SELECT kernel.state_as_of(%s, %s, %s)", [node_ids, edge_ids, offset]
        )

    async def find_agent(self, identity: dict[str, str]) -> str | None:
        return await self.read_value(
            "find_agent",
            "SELECT id FROM kernel.nodes WHERE type = 'Agent' AND identity @> %s "
            "ORDER BY created_offset LIMIT 1",
            [Jsonb({k: v.lower() for k, v in identity.items()})],
        )

    async def cypher(
        self, query: str, params: dict[str, Any], columns: list[str], limit: int, timeout_ms: int = 5000
    ) -> list[list[str | None]]:
        """Run a read-only Cypher query; returns raw agtype text per column, at most limit rows."""
        tag = dollar_tag(query)
        column_list = ", ".join(f"c{i} ag_catalog.agtype" for i in range(len(columns)))
        sql = f"SELECT * FROM ag_catalog.cypher('{GRAPH}', {tag}{query}{tag}, %s) AS ({column_list})"
        with otel.span(otel.SPAN_KERNEL_READ, **{otel.ATTR_READ: "cypher"}) as span:
            async with self.reader.connection() as conn, conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                cur = conn.cursor(row_factory=tuple_row)
                await cur.execute(sql, [Agtype(json.dumps(params))])
                rows = await cur.fetchmany(limit)
                otel.set_attributes(span, **{otel.ATTR_ROWS: len(rows)})
                return [list(r) for r in rows]


async def _configure_reader(conn: psycopg.AsyncConnection[Any]) -> None:
    """Reader sessions resolve AGE names and send Agtype values with the agtype type oid."""
    await conn.execute("SET search_path = ag_catalog, kernel, public")
    info = await psycopg.types.TypeInfo.fetch(conn, "ag_catalog.agtype")
    if info is None:
        raise RuntimeError("Apache AGE is not installed in this database")

    class AgtypeDumper(Dumper):
        oid = info.oid

        def dump(self, obj: Any) -> bytes:
            return str(obj).encode()

    conn.adapters.register_dumper(Agtype, AgtypeDumper)
    await conn.commit()


def _vector(embedding: list[float] | None) -> str | None:
    return None if embedding is None else "[" + ",".join(repr(float(x)) for x in embedding) + "]"

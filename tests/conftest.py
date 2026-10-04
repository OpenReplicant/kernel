"""Test fixtures. Tests need a Postgres with AGE and pgvector: `make db` (or `make test`) starts one.

Each test gets a fresh database cloned from a template that has the kernel SQL and the
reference pack applied.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from kernel import admin

WRITER_PASSWORD = os.environ.get("WMK_WRITER_PASSWORD", "writer")
READER_PASSWORD = os.environ.get("WMK_READER_PASSWORD", "reader")
TEMPLATE = "wmk_test_template"


class Rejected(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(f"{detail.get('problem')}: {detail.get('detail')}")
        self.detail = detail
        self.problem = detail.get("problem")
        self.rule = detail.get("rule")


class KernelDB:
    """SQL-level access: write functions called as kernel_writer, queries as superuser."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dsn = admin.dsn_for(admin.admin_dsn(), name)
        self.admin = psycopg.connect(self.dsn, autocommit=True)
        self.admin.execute('SET search_path = ag_catalog, "$user", public')
        self.writer = psycopg.connect(self.dsn, autocommit=True)
        self.writer.execute("SET ROLE kernel_writer")

    def close(self) -> None:
        self.admin.close()
        self.writer.close()

    def _call(self, function: str, payload: dict[str, Any], agent: str | None) -> dict[str, Any]:
        try:
            row = self.writer.execute(f"SELECT kernel.{function}(%s, %s)", [Jsonb(payload), agent]).fetchone()
        except psycopg.Error as exc:
            if exc.sqlstate == "WMK01" and exc.diag.message_detail:
                raise Rejected(json.loads(exc.diag.message_detail)) from None
            raise
        assert row is not None
        return row[0]

    def write(self, payload: dict[str, Any], agent: str | None) -> dict[str, Any]:
        return self._call("write", payload, agent)

    def ingest(self, source: dict[str, Any], agent: str) -> dict[str, Any]:
        return self._call("ingest_source", source, agent)

    def cite(self, cite: dict[str, Any], agent: str) -> dict[str, Any]:
        return self._call("cite", cite, agent)

    def q(self, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        return self.admin.execute(query, params or []).fetchall()

    def one(self, query: str, params: list[Any] | None = None) -> Any:
        rows = self.q(query, params)
        return rows[0][0] if rows else None

    def head(self) -> int:
        return int(self.one("SELECT kernel.head_offset()"))

    def register(self, name: str = "test-agent", trust: str = "medium", kind: str = "machine") -> str:
        identity = {"profile": name} if kind == "machine" else {}
        result = self.write(
            {
                "claim": {"text": f"{name} registered", "basis": "observed", "modality": "descriptive"},
                "read_at_offset": self.head(),
                "ops": [
                    {
                        "op": "create",
                        "type": "Agent",
                        "kind": kind,
                        "name": name,
                        "identity": identity,
                        "self": True,
                        "trust_level": trust,
                    }
                ],
            },
            None,
        )
        return str(result["agent_id"])

    def source(self, agent: str, content: str, **extra: Any) -> str:
        """Ingest a one-chunk source; returns its first chunk id."""
        return str(self.ingest({"content": content, **extra}, agent)["chunks"][0]["id"])

    def claim(
        self,
        agent: str,
        text: str,
        ops: list[dict[str, Any]],
        *,
        source: str | None = None,
        basis: str = "reported",
        modality: str = "descriptive",
        read_at: int | None = None,
        **claim_extra: Any,
    ) -> dict[str, Any]:
        claim: dict[str, Any] = {"text": text, "basis": basis, "modality": modality, **claim_extra}
        if source:
            claim["source"] = source
        return self.write(
            {"claim": claim, "read_at_offset": self.head() if read_at is None else read_at, "ops": ops}, agent
        )

    def edge(self, edge_id: str) -> dict[str, Any]:
        return self.one("SELECT to_jsonb(e) FROM kernel.edges e WHERE id = %s", [edge_id])

    def node(self, node_id: str) -> dict[str, Any]:
        return self.one("SELECT to_jsonb(n) - 'embedding' FROM kernel.nodes n WHERE id = %s", [node_id])


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    try:
        psycopg.connect(admin.admin_dsn(), connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"no database at WMK_ADMIN_DSN ({exc.__class__.__name__}); run `make db` first", returncode=2
        )
    admin.create_database(TEMPLATE)
    admin.apply(TEMPLATE)
    admin.ensure_login_roles(WRITER_PASSWORD, READER_PASSWORD)
    yield TEMPLATE
    admin.drop_database(TEMPLATE)


@pytest.fixture
def dbname(template_db: str) -> Iterator[str]:
    name = f"wmk_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin.admin_dsn(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template_db)
            )
        )
    yield name
    admin.drop_database(name)


@pytest.fixture
def kdb(dbname: str) -> Iterator[KernelDB]:
    k = KernelDB(dbname)
    yield k
    k.close()


@pytest.fixture
def agent(kdb: KernelDB) -> str:
    return kdb.register()


def login_dsn(dbname: str, role: str) -> str:
    password = WRITER_PASSWORD if role == "wmk_writer" else READER_PASSWORD
    return admin.dsn_for(admin.admin_dsn(), dbname) + f" user={role} password={password}"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def gateway_client(dbname: str, embedder: Any = None) -> AsyncIterator[Any]:
    """An MCP client connected in-process to a gateway running the eval profile on `dbname`."""
    from pathlib import Path

    from mcp import Client

    from gateway import profiles
    from gateway.db import Kernel
    from gateway.embeddings import NoEmbedder
    from gateway.identity import ensure_agents
    from gateway.server import build_server
    from gateway.tools import Tools

    kernel = Kernel(login_dsn(dbname, "wmk_writer"), login_dsn(dbname, "wmk_reader"), max_size=2)
    await kernel.open()
    try:
        profile = profiles.load(Path(__file__).resolve().parent.parent / "profiles" / "eval.yaml")
        agents = await ensure_agents(kernel, profile)
        tools = Tools(kernel, embedder or NoEmbedder(), agents, profile.name)
        async with Client(build_server(tools)) as client:
            client.tools = tools  # type: ignore[attr-defined]
            yield client
    finally:
        await kernel.close()


@pytest.fixture
async def gateway(dbname: str) -> AsyncIterator[Any]:
    """An MCP client connected in-process to a gateway running the eval profile on a fresh database."""
    async with gateway_client(dbname) as client:
        yield client

"""Connections, the core install, and the activation context writers run in."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg

from kernel import config

ROOT = Path(__file__).resolve().parent.parent
CORE_SQL = ROOT / "db" / "core.sql"


def graph_name() -> str:
    return os.environ.get("KERNEL_GRAPH", "entity")


def connect(dsn: str | None = None) -> psycopg.Connection:
    """A connection with graph_path set to the entity's graph. Not autocommit: writes run
    in explicit transactions (`with conn.transaction():`)."""
    conn = psycopg.connect(dsn or os.environ["DATABASE_URL"])
    if conn.execute("SELECT 1 FROM ag_graph WHERE graphname = %s", (graph_name(),)).fetchone():
        conn.execute(f"SET graph_path = {graph_name()}")
    conn.commit()  # before install_core the graph doesn't exist yet
    return conn


def install_core(conn: psycopg.Connection) -> None:
    """Install or refresh the core (db/core.sql) and set the loop guard's limits from the
    startup config (kernel.config). Idempotent."""
    sql = CORE_SQL.read_text().replace("__GRAPH__", graph_name())
    limits = config.load()["limits"]
    with conn.transaction():
        allow_ddl(conn)
        conn.execute(sql)
        for key, value in limits.items():  # the loop guard's limits, from the startup config
            conn.execute("""INSERT INTO kernel.config VALUES (%s, %s::jsonb)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, str(int(value))))
    conn.execute(f"SET graph_path = {graph_name()}")
    conn.commit()


def allow_ddl(conn: psycopg.Connection) -> None:
    """Let this transaction create labels in the managed graph (the module loader only)."""
    conn.execute("SELECT set_config('kernel.allow_ddl', 'on', true)")


def set_activation(conn: psycopg.Connection, causation: str | None, depth: int) -> None:
    """Mark the current transaction as part of an activation: the rows it writes record the
    causation and depth, and the loop guard checks the depth."""
    conn.execute("SELECT set_config('kernel.causation', %s, true), set_config('kernel.depth', %s, true)",
                 (causation or "", str(int(depth))))


@contextmanager
def activation(conn: psycopg.Connection, causation: str | None = None, depth: int = 0):
    """A transaction written on behalf of an activation (see set_activation)."""
    with conn.transaction():
        set_activation(conn, causation, depth)
        yield conn


def activation_from_metadata(metadata: dict | None) -> tuple[str | None, int]:
    """(causation, depth) for a write made while handling a chain message: the bridge passes
    the outbox row's causation id and depth; the write is one level deeper."""
    metadata = metadata or {}
    causation = metadata.get("causation") or None
    depth = int(metadata.get("depth") or 0)
    return causation, depth + 1 if causation else 0

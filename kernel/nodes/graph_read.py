"""Shared chain node: run a read-only Cypher query (x/python).

Message in:  {"cypher": "MATCH (n:note) WHERE n.key = %(key)s RETURN n.key, properties(n)",
              "params": {"key": "alpha"}}
Message out: {"rows": [[...], ...]}

Parameters are passed as jsonb (what Cypher compares properties with). The query runs in a
READ ONLY transaction, so it can't write.
"""
import os

from psycopg.types.json import Jsonb

from kernel import db


def read(conn, cypher: str, params: dict | None = None) -> list[list]:
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(cypher, {k: Jsonb(v) for k, v in (params or {}).items()} or None).fetchall()
    return [list(r) for r in rows]


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        rows = read(conn, msg["cypher"], msg.get("params"))
    return {"msg": {"rows": rows}, "metadata": metadata, "msgType": msgType}

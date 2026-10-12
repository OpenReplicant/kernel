"""Shared chain node: nearest things by a vector property (x/python).

Message in:  {"label": "note", "property": "embedding", "vector": [0.1, 0.2, 0.9], "k": 5}
Message out: {"rows": [{"id": "7.1", "key": "alpha", "distance": 0.12}, ...]}

L2 distance on a promoted vector column, which the module's HNSW index serves.
"""
import os

from kernel import claims, db


def search(conn, label: str, vector: list[float], k: int = 5, prop: str = "embedding") -> list[dict]:
    claims._check_label(label)
    claims._check_label(prop)
    v = "[" + ",".join(str(float(x)) for x in vector) + "]"
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(f"""MATCH (n:{label}) WHERE n.{prop} IS NOT NULL
                                RETURN id(n)::text, n.key, (n.{prop} <-> %s::vector)::float8
                                ORDER BY n.{prop} <-> %s::vector LIMIT %s""", (v, v, int(k))).fetchall()
    return [{"id": i, "key": key, "distance": d} for i, key, d in rows]


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        rows = search(conn, msg["label"], msg["vector"], msg.get("k", 5), msg.get("property", "embedding"))
    return {"msg": {"rows": rows}, "metadata": metadata, "msgType": msgType}

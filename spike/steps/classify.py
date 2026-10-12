"""S0 step: on a new :doc vertex, adopt the topic of its nearest classified neighbour.

Runs on the RuleGo x/python node. Input: one outbox row (from bridge.py). Reads the graph
(vector + Cypher in one query) and writes back an :about edge. Writes carry the causation
(outbox row) and depth + 1, which the outbox trigger records for the edge it fires on.
"""
import json
import os

import psycopg
from psycopg.types.json import Jsonb


def Process(msg, metadata, msgType, dataType):
    row = json.loads(msg) if isinstance(msg, str) else msg
    out = {"seq": row["seq"], "action": "skipped"}
    if row["label"] == "doc" and row["op"] == "INSERT":
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            conn.execute("SET graph_path = s0")
            conn.execute("SELECT set_config('kernel.causation', %s, true), set_config('kernel.depth', %s, true)",
                         (f"outbox:{row['seq']}", str(row["depth"] + 1)))
            hit = conn.execute(
                """MATCH (d:doc), (o:doc)-[:about]->(t:topic)
                   WHERE id(d) = %s::graphid AND id(o) <> id(d)
                   RETURN id(t)::text, t.name, o.name, (o.embedding <-> d.embedding)::float8
                   ORDER BY o.embedding <-> d.embedding LIMIT 1""",
                (row["element_id"],)).fetchone()
            if hit:
                already = conn.execute(
                    "MATCH (d:doc)-[:about]->(t:topic) WHERE id(d) = %s::graphid AND id(t) = %s::graphid RETURN 1",
                    (row["element_id"], hit[0])).fetchone()
                if not already:  # delivery is at-least-once, so the write is idempotent
                    conn.execute(
                        """MATCH (d:doc), (t:topic) WHERE id(d) = %s::graphid AND id(t) = %s::graphid
                           CREATE (d)-[:about {method: 'inferred', source: %s, neighbour: %s}]->(t)""",
                        # values inside a Cypher property map bind as jsonb, not text
                        (row["element_id"], hit[0], Jsonb(f"outbox:{row['seq']}"), Jsonb(hit[2])))
                out.update(action="classified", topic=hit[1], neighbour=hit[2], distance=hit[3])
    return {"msg": out, "metadata": metadata, "msgType": msgType}

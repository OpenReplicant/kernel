"""notes: answer a new ping claim (an outbox row) with another ping about the same subject.

A deliberate write loop for the guard test. The new claim cites the change that caused it
(graph_write does that when no source is given).
"""
import os

from kernel import db
from kernel.nodes.graph_read import read


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        rows = read(conn, "MATCH (c:claim)-[:about]->(s) WHERE id(c)::text = %(claim)s #>> '{}' RETURN id(s)::text",
                    {"claim": msg["element_id"]})
    subject = rows[0][0]
    write = {"claims": [{"subject": {"id": subject}, "predicate": "notes.ping", "value": msg["depth"] + 1,
                         "method": "computed", "confidence": 1.0}]}
    return {"msg": write, "metadata": metadata, "msgType": msgType}

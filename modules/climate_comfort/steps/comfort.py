"""climate_comfort: on a new temperature or setpoint claim (an outbox row), judge the room.

cold below setpoint - band, warm above setpoint + band, else ok. The belief is written only
when it changes, derived from the temperature and setpoint claims it was judged from; it cites
the change that caused it (graph_write's default source).
"""
import os

from kernel import claims, db


def judge(celsius: float, setpoint: float, band: float) -> str:
    if celsius < setpoint - band:
        return "cold"
    if celsius > setpoint + band:
        return "warm"
    return "ok"


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    band = float((vars or {}).get("band", 0.5))
    nothing = {"msg": {}, "metadata": metadata, "msgType": msgType}
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        row = conn.execute("MATCH (c:claim)-[:about]->(r:room) WHERE id(c) = %s::graphid RETURN id(r)::text",
                           (msg["element_id"],)).fetchone()
        if row is None:
            return nothing
        room = row[0]
        temp = claims.held(conn, room, "climate.temperature")
        setp = claims.held(conn, room, "climate.setpoint")
        comfort = claims.held(conn, room, "climate.comfort")
    if not temp or not setp:
        return nothing  # no belief without both
    t, s = temp[-1], setp[-1]
    value = judge(t["value"], s["value"], band)
    if comfort and comfort[-1]["value"] == value:
        return nothing
    write = {"claims": [{"subject": {"id": room}, "predicate": "climate.comfort", "value": value,
                         "method": "inferred", "confidence": 0.9, "derived_from": [t["id"], s["id"]],
                         "replaces": "current"}]}
    return {"msg": write, "metadata": metadata, "msgType": msgType}

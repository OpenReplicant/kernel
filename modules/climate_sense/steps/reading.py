"""climate_sense: one reading {sensor, room, celsius, heater, at, seq} -> a write for graph_write.

The reading itself is the source. The temperature is always observed (it replaces the held
one); the heater state only when it differs from the held observation, so a steady heater
doesn't add a claim per reading.
"""
import os

from psycopg.types.json import Jsonb

from kernel import claims, db


def held_heater(room: str):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        row = conn.execute("MATCH (r:room) WHERE r.key = %s RETURN id(r)::text", (Jsonb(room),)).fetchone()
        if row is None:
            return None
        held = claims.held(conn, row[0], "climate.heater")
        return held[-1]["value"] if held else None


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    common = {"subject": "room", "method": "observed", "confidence": 1.0, "valid_from": msg["at"],
              "replaces": "current"}
    write = {
        "source": {"kind": "reading", "content": msg, "uri": f"sensor:{msg['sensor']}"},
        "things": [{"ref": "room", "label": "room", "key": msg["room"]}],
        "claims": [{**common, "predicate": "climate.temperature", "value": msg["celsius"], "span": "$.celsius"}],
    }
    if held_heater(msg["room"]) != msg["heater"]:
        write["claims"].append({**common, "predicate": "climate.heater", "value": msg["heater"], "span": "$.heater"})
    return {"msg": write, "metadata": metadata, "msgType": msgType}

"""climate_recall: {room} -> {room, state, text, claims}.

`state` maps each climate predicate to the held claim (id, value, method, confidence,
recorded_at); `text` renders it for a person or a model; `claims` lists the ids recalled.
"""
import os

from psycopg.types.json import Jsonb

from kernel import claims, db

PREDICATES = ("temperature", "setpoint", "comfort", "heater", "decision")


def recall(conn, room_key: str) -> dict:
    row = conn.execute("MATCH (r:room) WHERE r.key = %s RETURN id(r)::text", (Jsonb(room_key),)).fetchone()
    state = {}
    if row:
        for p in PREDICATES:
            held = claims.held(conn, row[0], f"climate.{p}")
            if held:
                h = held[-1]
                state[p] = {k: h.get(k) for k in ("id", "value", "method", "confidence", "recorded_at")}
    lines = [f"{room_key}:"] + [f"  {p} = {json_value(s['value'])} ({s['method']}, {s['confidence']}, at {s['recorded_at']})"
                                for p, s in state.items()]
    return {"room": room_key, "state": state, "text": "\n".join(lines), "claims": [s["id"] for s in state.values()]}


def json_value(v):
    return v if not isinstance(v, dict) else ", ".join(f"{k}: {x}" for k, x in v.items())


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        out = recall(conn, msg["room"])
    return {"msg": out, "metadata": metadata, "msgType": msgType}

"""climate_heater: {room, on, decision} -> switch the heater, then a write recording it.

Limits: the decision must be a held climate.decision claim about that room asking for this
state; a room's heater is not commanded twice within min_interval seconds. The request and the
world's answer are the source of an observed climate.heater_command claim derived from the
decision.
"""
import os
from datetime import datetime, timezone

import requests
from psycopg.types.json import Jsonb

from kernel import claims, db


def _seconds_since(ts: str) -> float:
    t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    vars = vars or {}
    room_key, on, decision = msg["room"], bool(msg["on"]), msg["decision"]
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        room = conn.execute("MATCH (r:room) WHERE r.key = %s RETURN id(r)::text", (Jsonb(room_key),)).fetchone()[0]
        held = [d["id"] for d in claims.held(conn, room, "climate.decision") if d["value"] == {"heater": on}]
        if decision not in held:
            raise ValueError(f"no held decision {decision} for {room_key} heater={on}: refusing to act")
        last = claims.held(conn, room, "climate.heater_command")
    if last and _seconds_since(last[-1]["recorded_at"]) < float(vars.get("min_interval", 1.0)):
        raise RuntimeError(f"rate limited: {room_key} heater commanded {last[-1]['recorded_at']}")
    request = {"room": room_key, "on": on}
    url = f"{vars.get('simulator_url', 'http://127.0.0.1:9191')}/heater"
    r = requests.post(url, json=request, timeout=10)
    r.raise_for_status()
    response = r.json()
    write = {
        "source": {"kind": "effect", "content": {"request": request, "response": response, "decision": decision},
                   "uri": url},
        "claims": [{"subject": {"label": "room", "key": room_key}, "predicate": "climate.heater_command",
                    "value": response["heater"], "method": "observed", "confidence": 1.0,
                    "span": "$.response.heater", "derived_from": [decision], "replaces": "current"}],
    }
    return {"msg": write, "metadata": metadata, "msgType": msgType}

"""climate_chat: the model's reply -> an inferred setpoint (for graph_write), or nothing.

The model call is the source (span $.reply.text); the claim is `inferred` because a model
interpreted the person's words. A reply naming no known room (the rooms the prompt listed, passed along as `rooms`), or not
JSON, writes nothing.
"""
import json


def parse(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    reply = msg["model_reply"]
    got = parse(reply["text"]) or {}
    room, celsius = got.get("room"), got.get("celsius")
    understood = room in msg.get("rooms", []) and isinstance(celsius, (int, float)) and not isinstance(celsius, bool)
    out = {"understood": understood, "model_reply": reply}
    if understood:
        out.update({
            "source": {"id": reply["source"]},
            "things": [{"ref": "room", "label": "room", "key": room}],
            "claims": [{"subject": "room", "predicate": "climate.setpoint", "value": celsius, "method": "inferred",
                        "confidence": float((vars or {}).get("confidence", 0.8)), "span": "$.reply.text",
                        "replaces": "current"}]})
    return {"msg": out, "metadata": metadata, "msgType": msgType}

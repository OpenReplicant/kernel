"""climate_sense: a setpoint command {room, celsius, by} -> a stated claim (the command is the source)."""


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    write = {
        "source": {"kind": "command", "content": msg, "uri": f"person:{msg.get('by', 'unknown')}"},
        "things": [{"ref": "room", "label": "room", "key": msg["room"]}],
        "claims": [{"subject": "room", "predicate": "climate.setpoint", "value": msg["celsius"],
                    "method": "stated", "confidence": 1.0, "span": "$.celsius", "replaces": "current"}],
    }
    return {"msg": write, "metadata": metadata, "msgType": msgType}

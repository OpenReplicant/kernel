"""climate_control: the recorded decision -> the effector's request {act, room, on, decision}.

If the decision was skipped (another tick decided first), there is nothing to act on.
"""


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    written = msg.get("written") or {}
    if not written.get("claims"):
        return {"msg": {"act": False, "room": msg["room"], "skipped": written.get("skipped")},
                "metadata": metadata, "msgType": msgType}
    [decision] = written["claims"]
    return {"msg": {"act": True, "room": msg["room"], "on": msg["on"], "decision": decision},
            "metadata": metadata, "msgType": msgType}

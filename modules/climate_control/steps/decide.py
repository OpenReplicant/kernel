"""climate_control: recalled room state -> a decision (or none).

Policy: cold -> heater on; warm -> heater off; ok -> leave it. The heater's current state is
the latest of the observed heater state and the entity's own last decision (a decision not yet
reflected in a reading still counts, so a tick doesn't repeat it). When the policy wants a
change, the decision is an inferred claim derived from the recalled comfort belief (and heater
observation), citing the recalled context as its source.
"""


def decide(state: dict) -> tuple[bool | None, bool]:
    """(wanted heater state or None, current heater state)."""
    heater, decision = state.get("heater"), state.get("decision")
    current = heater["value"] if heater else False
    if decision and (not heater or decision["recorded_at"] > heater["recorded_at"]):
        current = decision["value"]["heater"]
    comfort = (state.get("comfort") or {}).get("value")
    want = {"cold": True, "warm": False}.get(comfort)
    return want, current


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    state = msg["state"]
    want, current = decide(state)
    if want is None or want == current:
        return {"msg": {"act": False, "room": msg["room"]}, "metadata": metadata, "msgType": msgType}
    derived = [state["comfort"]["id"]] + ([state["heater"]["id"]] if "heater" in state else [])
    out = {
        "act": True, "room": msg["room"], "on": want,
        "source": {"kind": "recall", "content": {"text": msg["text"], "state": state},
                   "uri": "chain:climate_recall_room"},
        "claims": [{"subject": {"label": "room", "key": msg["room"]}, "predicate": "climate.decision",
                    "value": {"heater": want}, "method": "inferred", "confidence": 0.9,
                    "derived_from": derived, "replaces": "current",
                    # skip if another tick decided since this one recalled
                    "if_current": [state["decision"]["id"]] if "decision" in state else []}],
    }
    return {"msg": out, "metadata": metadata, "msgType": msgType}

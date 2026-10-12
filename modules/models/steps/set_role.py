"""models_set_role: a person chooses a role's model, budget or parameters -> stated claims.

In: {"role": "<module>.<role>", "model": "<model key>", "budget": {"usd": .., "tokens": ..},
     "params": {...}, "by": "<person>"}   (any of model, budget, params)
The command is the source; each choice replaces the role's held claim (a module's defaulted
choice, or an earlier stated one). A new budget starts counting spend afresh.
"""


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    role = {"label": "model_role", "key": msg["role"]}
    common = {"subject": role, "method": "stated", "confidence": 1.0, "replaces": "current"}
    out = []
    if "model" in msg:
        out.append({**common, "predicate": "models.uses", "object": {"label": "model", "key": msg["model"]},
                    "span": "$.model"})
    for f in ("budget", "params"):
        if f in msg:
            out.append({**common, "predicate": f"models.{f}", "value": msg[f], "span": f"$.{f}"})
    if not out:
        raise ValueError("models_set_role needs one of model, budget, params")
    write = {"source": {"kind": "command", "content": msg, "uri": f"person:{msg.get('by', 'unknown')}"},
             "claims": out}
    return {"msg": write, "metadata": metadata, "msgType": msgType}

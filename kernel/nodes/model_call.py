"""Shared chain node: call a model for a role, and record the call in the graph (x/python).

Message in:
    {"model_call": {"role": "interpreter" | "<module>.<role>",
                    "prompt": "..." | "messages": [{"role": "user", "content": "..."}],
                    "system": "...", "inputs": {...}},          # system and inputs optional
     ...}                                                       # other fields pass through
A bare role name is the chain's module's role. `inputs` is what the prompt was made from (a
person's message, recalled context), kept in the record so the call can be traced to it.

Message out: the other fields, plus
    {"model_reply": {"text", "model", "usage", "cost_usd", "source": "<model_call source id>"}}

Steps: resolve the role from held claims (kernel.models.resolve); refuse when the role's budget
is spent; call the endpoint; record a `model_call` source with the request, the reply, the
model that answered, tokens, cost, timing and the claims the call was resolved from. A refused
or failed call is recorded too (status `refused` / `failed`, no spend) and the node fails.
"""
import os
import time

from kernel import claims, db, models, providers


def run(conn, req: dict, module: str | None, causation: str | None = None, depth: int = 0) -> dict:
    role = req["role"] if "." in req["role"] or not module else f"{module}.{req['role']}"
    messages = req.get("messages") or [{"role": "user", "content": req["prompt"]}]
    request = {"system": req.get("system"), "messages": messages}
    content = {"role": role, "request": request, "inputs": req.get("inputs"), "at": claims.now(conn),
               "module": module}
    conn.commit()
    try:
        cfg = models.resolve(conn, role)
        content.update(model=cfg["model"], model_id=cfg["model_id"], endpoint=cfg["endpoint"], api=cfg["api"],
                       params=cfg["params"], resolved_from=cfg["resolved_from"])
        content["spent_before"] = models.check_budget(conn, cfg)
        conn.commit()
    except (models.Unresolved, models.OverBudget) as e:
        content.update(status="refused", error=str(e))
        models.record(conn, content, causation, depth)
        raise
    started = time.monotonic()
    try:
        reply = providers.call({**cfg, **request})
    except providers.CallFailed as e:
        content.update(status="failed", error=str(e), duration_ms=round((time.monotonic() - started) * 1000))
        models.record(conn, content, causation, depth)
        raise
    cost = models.cost_usd(cfg["prices"], reply["usage"])
    content.update(status="ok", reply={"text": reply["text"], "stop_reason": reply["stop_reason"]},
                   served_by=reply["model"], usage=reply["usage"], tokens=models.tokens(reply["usage"]),
                   cost_usd=cost, duration_ms=round((time.monotonic() - started) * 1000))
    if reply.get("reported_cost_usd") is not None:
        content["reported_cost_usd"] = reply["reported_cost_usd"]
    source = models.record(conn, content, causation, depth)
    return {"text": reply["text"], "model": reply["model"], "usage": reply["usage"], "cost_usd": cost,
            "source": source}


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    req = msg.get("model_call")
    if not req or not req.get("role") or not (req.get("prompt") or req.get("messages")):
        raise ValueError("model_call needs {'model_call': {'role', 'prompt' or 'messages'}}")
    causation, depth = db.activation_from_metadata(metadata)
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        reply = run(conn, req, (vars or {}).get("module"), causation, depth)
    rest = {k: v for k, v in msg.items() if k != "model_call"}
    return {"msg": {**rest, "model_reply": reply}, "metadata": metadata, "msgType": msgType}

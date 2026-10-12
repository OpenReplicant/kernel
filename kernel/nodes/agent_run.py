"""Shared chain node: an agent works on a task with the sandbox as its only tool, and the run is
recorded in the graph (x/python). docs/HARNESSES.md.

Message in:
    {"agent_run": {"role": "agent" | "<module>.<role>", "task": "...",
                   "system": "...", "inputs": {...}, "max_turns": 5},   # all but role, task optional
     ...}                                                               # other fields pass through
A bare role name is the chain's module's role. `inputs` is what the task was made from.

Message out: the other fields, plus
    {"agent_result": {"text", "model", "usage", "cost_usd", "turns", "tool_calls",
                      "source": "<agent_run source id>"}}

Steps, as for model_call: resolve the role from held claims; refuse when the role's budget is
spent (model calls and agent runs count against the same budget); run the endpoint's harness
(kernel.harnesses) with the sandbox server from the startup config as the only tool; record an
`agent_run` source with the request, the tools the agent had, the transcript (every message,
tool call and tool result), the model that answered, tokens, cost and timing. A refused or
failed run is recorded too, with as much of the transcript as was seen, and the node fails.

The agent's own sandbox runs are in the transcript; they are not separate `sandbox_run`
sources. A claim about the agent's work is made from a run the kernel does itself
(the sandbox_run node), citing this one in its `inputs`.
"""
import os
import time

from kernel import claims, config, db, harnesses, models


def sandbox_tool(cfg: dict) -> dict:
    srv = cfg["sandbox"]["server"]
    if srv.get("url"):
        return {"name": "sandbox", "url": srv["url"]}
    return {"name": "sandbox", "command": [*srv["command"], "--config", cfg["file"]],
            "env": {"PYTHONPATH": str(config.ROOT)}}


def run(conn, req: dict, module: str | None, causation: str | None = None, depth: int = 0,
        cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    role = req["role"] if "." in req["role"] or not module else f"{module}.{req['role']}"
    limit = cfg["agents"]["max_turns"]
    tools = [sandbox_tool(cfg)]
    request = {"system": req.get("system"), "task": req["task"], "tools": [t["name"] for t in tools],
               "max_turns": min(int(req.get("max_turns") or limit), limit)}
    content = {"role": role, "request": request, "inputs": req.get("inputs"), "at": claims.now(conn),
               "module": module, "config": cfg["file"]}
    conn.commit()
    try:
        m = models.resolve(conn, role)
        content.update(model=m["model"], model_id=m["model_id"], endpoint=m["endpoint"], api=m["api"],
                       params=m["params"], resolved_from=m["resolved_from"])
        content["spent_before"] = models.check_budget(conn, m)
        conn.commit()
    except (models.Unresolved, models.OverBudget) as e:
        content.update(status="refused", error=str(e))
        models.record(conn, content, causation, depth, "agent_run")
        raise
    started = time.monotonic()
    try:
        out = harnesses.run({**m, **request, "timeout_s": cfg["agents"]["timeout_s"]}, tools)
    except harnesses.CallFailed as e:
        seen = getattr(e, "seen", {})
        content.update(status="failed", error=str(e), duration_ms=round((time.monotonic() - started) * 1000),
                       tools_given=seen.get("tools"), transcript=seen.get("transcript"))
        if seen.get("usage"):  # tokens a failed run still spent count against the budget
            content.update(usage=seen["usage"], tokens=models.tokens(seen["usage"]),
                           cost_usd=models.cost_usd(m["prices"], seen["usage"]))
        models.record(conn, content, causation, depth, "agent_run")
        raise
    cost = models.cost_usd(m["prices"], out["usage"])
    tool_calls = sum(1 for t in out["transcript"] if t["type"] == "tool_call")
    content.update(status="ok", reply={"text": out["text"], "stop_reason": out["stop_reason"]},
                   tools_given=out["tools"], transcript=out["transcript"], turns=out["turns"],
                   tool_calls=tool_calls, served_by=out["model"], usage=out["usage"],
                   tokens=models.tokens(out["usage"]), cost_usd=cost,
                   duration_ms=round((time.monotonic() - started) * 1000))
    if out.get("reported_cost_usd") is not None:
        content["reported_cost_usd"] = out["reported_cost_usd"]
    source = models.record(conn, content, causation, depth, "agent_run")
    return {"text": out["text"], "model": out["model"], "usage": out["usage"], "cost_usd": cost,
            "turns": out["turns"], "tool_calls": tool_calls, "source": source}


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    req = msg.get("agent_run")
    if not req or not req.get("role") or not req.get("task"):
        raise ValueError("agent_run needs {'agent_run': {'role', 'task'}}")
    causation, depth = db.activation_from_metadata(metadata)
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        result = run(conn, req, (vars or {}).get("module"), causation, depth)
    rest = {k: v for k, v in msg.items() if k != "agent_run"}
    return {"msg": {**rest, "agent_result": result}, "metadata": metadata, "msgType": msgType}

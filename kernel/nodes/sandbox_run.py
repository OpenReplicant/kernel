"""Shared chain node: run untrusted code in the sandbox, and record the run (x/python).

Message in:
    {"sandbox_run": {"command": ["python", "test.py"], "files": {"test.py": "..."},
                     "image": "python", "timeout_s": 5, "memory": "128m",    # limits optional
                     "inputs": {...}},                                       # optional
     ...}                                                                    # passes through
`inputs` says what the run is for (e.g. the model call that wrote the code); it is kept in
the record.

Message out: the other fields, plus
    {"sandbox_result": {"exit_code", "stdout", "stderr", "timed_out", "duration_s", "backend",
                        "isolated", "image", "limits", "source": "<sandbox_run source id>"}}

The node is an MCP client of the sandbox server named in the startup config
(`sandbox.server`: a command for stdio, or a url), so the runtime behind it can be swapped
without touching chains. The server gets no secrets: a stdio server is started with only
PATH-like variables (the MCP SDK's default) and the config file's path. Code that fails or
times out is an ordinary result; a run the server refuses or can't start is recorded with
status `refused` and the node fails.
"""
import asyncio
import json
import os

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from kernel import claims, config, db

RUN_FIELDS = ("command", "files", "image", "timeout_s", "cpus", "memory", "pids", "max_output")


def server_params(cfg: dict):
    srv = cfg["sandbox"]["server"]
    if srv.get("url"):
        return srv["url"]
    return StdioServerParameters(command=srv["command"][0], args=[*srv["command"][1:], "--config", cfg["file"]],
                                 cwd=str(config.ROOT))


async def _call(cfg: dict, args: dict) -> dict:
    async with Client(server_params(cfg)) as client:
        res = await client.call_tool("sandbox_run", args)
    text = "".join(getattr(c, "text", "") for c in res.content)
    if res.is_error:
        raise RuntimeError(f"sandbox refused the run: {text[:500]}")
    out = res.structured_content
    if isinstance(out, dict) and set(out) == {"result"}:
        out = out["result"]
    return out if isinstance(out, dict) else json.loads(text)


def run(conn, req: dict, causation: str | None = None, depth: int = 0, cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    args = {k: req[k] for k in RUN_FIELDS if req.get(k) is not None}
    content = {"request": args, "inputs": req.get("inputs"), "at": claims.now(conn), "config": cfg["file"],
               "server": cfg["sandbox"]["server"]}
    conn.commit()
    try:
        result = asyncio.run(_call(cfg, args))
        content.update(status="ok", result=result)
    except Exception as e:  # the server refused or couldn't start the run: recorded, then raised
        content.update(status="refused", error=str(e)[:1000])
        _record(conn, content, causation, depth)
        raise
    return {**result, "source": _record(conn, content, causation, depth)}


def _record(conn, content, causation, depth) -> str:
    conn.rollback()
    with db.activation(conn, causation, depth):
        return claims.ensure_source(conn, "sandbox_run", json.loads(json.dumps(content)),
                                    f"sandbox:{(content.get('result') or {}).get('backend', 'unknown')}")


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    req = msg.get("sandbox_run")
    if not req or not req.get("command"):
        raise ValueError("sandbox_run needs {'sandbox_run': {'command': [...], 'files': {...}}}")
    causation, depth = db.activation_from_metadata(metadata)
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        result = run(conn, req, causation, depth)
    rest = {k: v for k, v in msg.items() if k != "sandbox_run"}
    return {"msg": {**rest, "sandbox_result": result}, "metadata": metadata, "msgType": msgType}

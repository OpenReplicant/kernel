"""Agent harnesses: a model working through tools until the task is done (docs/HARNESSES.md).

A harness is chosen by the endpoint's API kind, as a provider is for a one-shot call:

    run(cfg, tools) -> {"text", "stop_reason", "model", "usage", "transcript", "tools", "turns",
                        "reported_cost_usd"?}

`cfg` is what kernel.models.resolve() returned plus `system`, `task` (the first user message),
`max_turns` and `timeout_s`. `tools` are the MCP servers the agent may use, each
`{"name", "command": [...]}` (stdio) or `{"name", "url"}` (streamable HTTP); they are the
agent's only tools. A stdio server is started with an empty environment plus `env` (if given):
the harness's own environment (API keys, DATABASE_URL) never reaches it.

`transcript` is what happened, in order: `{"type": "text", "text"}`, `{"type": "tool_call",
"id", "name", "input"}`, `{"type": "tool_result", "id", "content", "is_error"}`. `tools` is the
tool list the agent was actually given (reported by the harness, not assumed).

A run that fails part-way raises Failed, carrying what was seen so far, so it can be recorded.

Harnesses:
- `claude_code`: `claude -p` with no built-in tools, no settings and no MCP servers but the ones
  given, our system prompt, an empty working directory and a turn limit; `--output-format
  stream-json` gives the transcript. `$KERNEL_CLAUDE_CMD` replaces the `claude` command.
"""
import json
import os
import subprocess
import tempfile

from kernel.providers import CallFailed, claude_cmd, cli_usage


class Failed(CallFailed):
    def __init__(self, message: str, seen: dict | None = None):
        super().__init__(message)
        self.seen = seen or {}


def _stdio_env(server: dict) -> dict:
    """What a stdio tool server is started with: PATH and HOME (to find its runtime), nothing
    else of ours, plus the server's own `env`."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"),
            **server.get("env", {})}


def claude_mcp_config(tools: list[dict]) -> dict:
    """The --mcp-config for `claude -p`. Claude Code starts stdio servers with its whole
    environment (docs/SPIKE_NOTES.md, S5), so each is wrapped in `env -i`."""
    servers = {}
    for t in tools:
        if t.get("url"):
            servers[t["name"]] = {"type": "http", "url": t["url"]}
        else:
            env = [f"{k}={v}" for k, v in _stdio_env(t).items()]
            servers[t["name"]] = {"command": "env", "args": ["-i", *env, *t["command"]]}
    return {"mcpServers": servers}


def _claude_code(cfg: dict, tools: list[dict]) -> dict:
    if cfg["params"]:
        raise Failed(f"the claude_code harness takes no parameters, got {sorted(cfg['params'])}")
    allowed = [f"mcp__{t['name']}" for t in tools]  # every tool of each server given
    argv = [*claude_cmd(), "-p", "--model", cfg["model_id"],
            "--tools", "", "--strict-mcp-config", "--mcp-config", json.dumps(claude_mcp_config(tools)),
            *(["--allowedTools", *allowed] if allowed else []),
            "--setting-sources", "", "--no-session-persistence", "--max-turns", str(cfg["max_turns"]),
            "--system-prompt", cfg.get("system") or "You are a careful agent.",
            "--output-format", "stream-json", "--verbose"]
    with tempfile.TemporaryDirectory(prefix="kernel-agent-") as empty:  # no project files or CLAUDE.md
        try:
            r = subprocess.run(argv, input=cfg["task"], capture_output=True, text=True, cwd=empty,
                               timeout=cfg["timeout_s"])
        except subprocess.TimeoutExpired as e:
            raise Failed(f"claude -p ran past its {cfg['timeout_s']} s timeout",
                         _parse((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes)
                                else e.stdout or "")) from e
        except FileNotFoundError as e:
            raise Failed(f"claude -p: {e}") from e
    seen = _parse(r.stdout)
    result = seen.pop("result", None)
    if result is None:
        raise Failed(f"claude -p exited {r.returncode} without a result: {(r.stderr or r.stdout)[-300:]}", seen)
    down = [s["name"] for s in seen.get("mcp_servers", []) if s.get("status") != "connected"]
    if down:
        raise Failed(f"tool servers not connected: {down}", seen)
    seen["model"], seen["usage"] = cli_usage(result, cfg["model_id"])
    seen["reported_cost_usd"] = result.get("total_cost_usd")
    seen["turns"] = result.get("num_turns")
    if result.get("is_error") or result.get("subtype") != "success":
        raise Failed(f"claude -p ended with {result.get('subtype')}: {str(result.get('result') or '')[-300:]}", seen)
    seen.update(text=result.get("result") or "", stop_reason=result.get("stop_reason"))
    return seen


def _parse(stream: str) -> dict:
    """claude -p's stream-json events -> {tools, mcp_servers, transcript, result?}."""
    seen = {"tools": None, "mcp_servers": [], "transcript": []}
    for line in stream.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = e.get("type")
        if kind == "system" and e.get("subtype") == "init":
            seen["tools"], seen["mcp_servers"] = e.get("tools"), e.get("mcp_servers") or []
        elif kind in ("assistant", "user"):
            content = e.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "text" and kind == "assistant":
                    seen["transcript"].append({"type": "text", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    seen["transcript"].append({"type": "tool_call", "id": b.get("id"), "name": b.get("name"),
                                               "input": b.get("input")})
                elif b.get("type") == "tool_result":
                    seen["transcript"].append({"type": "tool_result", "id": b.get("tool_use_id"),
                                               "content": _text(b.get("content")), "is_error": bool(b.get("is_error"))})
        elif kind == "result":
            seen["result"] = e
    return seen


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else json.dumps(content)


HARNESSES = {"claude_code": _claude_code}


def run(cfg: dict, tools: list[dict]) -> dict:
    if cfg.get("api") not in HARNESSES:
        raise Failed(f"endpoint {cfg.get('endpoint')} has api {cfg.get('api')!r}, which has no agent harness; "
                     f"one of {sorted(HARNESSES)}")
    return HARNESSES[cfg["api"]](cfg, tools)

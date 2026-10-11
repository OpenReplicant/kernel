"""Calling a model endpoint, by API kind. Used by the model_call sub-chain's invoke step.

call(cfg) takes what models.resolve() returned plus `system` and `messages`, and returns
{"text", "stop_reason", "model", "usage"} with usage in Anthropic's field names
(input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens).
Transient failures (rate limits, 5xx, connection errors) raise pclib.Retryable.

API kinds: `anthropic` (Messages API), `openai` (any OpenAI-compatible chat endpoint), and
`claude_code`: a one-shot `claude -p` on the logged-in Claude Code session, so calls count
against that subscription's usage instead of an API key. It runs with no tools, no settings,
no MCP servers, our system prompt, in an empty directory, and reports the model that answered.
"""
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

from pclib import Retryable


def _key(cfg) -> str | None:
    env = cfg.get("key_env")
    if not env:
        return None
    key = os.environ.get(env)
    if not key:
        raise RuntimeError(f"endpoint {cfg.get('endpoint')} needs its key in ${env}, which is not set")
    return key


def _anthropic(cfg) -> dict:
    import inspect

    import anthropic
    client = anthropic.Anthropic(api_key=_key(cfg), base_url=cfg.get("base_url"))
    # Parameters the SDK names go as keywords; any other (e.g. sampling settings an older model
    # or another Messages-compatible endpoint takes) is sent as-is in the request body.
    known = set(inspect.signature(client.messages.create).parameters)
    kwargs = {"model": cfg["model_id"], "messages": cfg["messages"]}
    extra = {}
    for name, v in cfg["params"].items():
        (kwargs if name in known else extra)[name] = v
    if extra:
        kwargs["extra_body"] = extra
    if cfg.get("system"):
        kwargs["system"] = cfg["system"]
    try:
        msg = client.messages.create(**kwargs)
    except anthropic.RateLimitError as e:
        raise Retryable(f"rate limited: {e.message}") from e
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            raise Retryable(f"server error {e.status_code}: {e.message}") from e
        raise
    except anthropic.APIConnectionError as e:
        raise Retryable(f"connection error: {e}") from e
    u = msg.usage
    return {"text": "".join(b.text for b in msg.content if b.type == "text"),
            "stop_reason": msg.stop_reason, "model": msg.model,
            "usage": {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                      "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None) or 0,
                      "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None) or 0}}


def _openai(cfg) -> dict:
    """Any OpenAI-compatible chat-completions endpoint (local servers, other providers)."""
    messages = ([{"role": "system", "content": cfg["system"]}] if cfg.get("system") else []) + cfg["messages"]
    body = json.dumps({"model": cfg["model_id"], "messages": messages, **cfg["params"]}).encode()
    headers = {"Content-Type": "application/json"}
    key = _key(cfg)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions", body, headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code >= 500:
            raise Retryable(f"HTTP {e.code} from {cfg['base_url']}") from e
        raise RuntimeError(f"HTTP {e.code} from {cfg['base_url']}: {e.read()[:500]!r}") from e
    except urllib.error.URLError as e:
        raise Retryable(f"connection error: {e.reason}") from e
    choice = resp["choices"][0]
    u = resp.get("usage") or {}
    return {"text": choice["message"].get("content") or "", "stop_reason": choice.get("finish_reason"),
            "model": resp.get("model", cfg["model_id"]),
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}


def _claude_code(cfg) -> dict:
    """`claude -p` with model_id as --model (an alias such as `haiku`, or a full id). One user
    message only; parameters other than none are refused (the CLI takes no sampling settings).
    `$PC_CLAUDE_CMD` overrides the command (tests use a fake)."""
    msgs = cfg["messages"]
    if len(msgs) != 1 or msgs[0]["role"] != "user" or not isinstance(msgs[0]["content"], str):
        raise ValueError("the claude_code endpoint takes exactly one user message with text content")
    if cfg["params"]:
        raise ValueError(f"the claude_code endpoint takes no parameters, got {sorted(cfg['params'])}")
    argv = [*os.environ.get("PC_CLAUDE_CMD", "claude").split(), "-p", "--model", cfg["model_id"],
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
            "--system-prompt", cfg.get("system") or "You are a helpful assistant.", "--output-format", "json"]
    with tempfile.TemporaryDirectory(prefix="pc-claude-") as empty:     # no project files or CLAUDE.md
        try:
            r = subprocess.run(argv, input=msgs[0]["content"], capture_output=True, text=True, cwd=empty,
                               timeout=600)
        except subprocess.TimeoutExpired as e:
            raise Retryable("claude -p timed out") from e
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p exited {r.returncode}: {(r.stderr or r.stdout)[-500:]}") from None
    if out.get("is_error") or r.returncode != 0:
        text = str(out.get("result") or r.stderr)[-500:]
        if any(w in text.lower() for w in ("rate limit", "overloaded", "usage limit", "529", "429")):
            raise Retryable(f"claude -p: {text}")
        raise RuntimeError(f"claude -p failed: {text}")
    by_model = out.get("modelUsage") or {}
    served = max(by_model, key=lambda m: by_model[m].get("outputTokens", 0)) if by_model else cfg["model_id"]
    u = out.get("usage") or {}
    return {"text": out.get("result") or "", "stop_reason": out.get("stop_reason"), "model": served,
            "usage": {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                      "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                      "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0)}}


APIS = {"anthropic": _anthropic, "openai": _openai, "claude_code": _claude_code}


def call(cfg) -> dict:
    if cfg.get("api") not in APIS:
        raise ValueError(f"endpoint {cfg.get('endpoint')} has api {cfg.get('api')!r}; one of {sorted(APIS)}")
    return APIS[cfg["api"]](cfg)

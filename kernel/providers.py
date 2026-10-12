"""Calling a model endpoint, by API kind (ported from the second iteration's
modules/evaluation/providers.py).

call(cfg) takes what kernel.models.resolve() returned plus `system` and `messages`, and returns
{"text", "stop_reason", "model", "usage"}, with usage in Anthropic's field names (input_tokens,
output_tokens, cache_read_input_tokens, cache_creation_input_tokens), and for `claude_code` also
`reported_cost_usd` (what the CLI says the call would cost at list price).

API kinds:
- `anthropic`: the Messages API through the Anthropic SDK.
- `openai`: any OpenAI-compatible chat-completions endpoint (local servers, other providers).
- `litellm`: LiteLLM's SDK (an optional dependency), `model_id` being LiteLLM's
  `provider/model` (e.g. `openai/gpt-x`, `anthropic/claude-haiku-5-5`), `base_url` its
  `api_base`; it routes to any provider it supports. (LiteLLM's proxy is an `openai` endpoint.)
- `claude_code`: a one-shot `claude -p` on the logged-in Claude Code session, so calls count
  against that subscription instead of an API key. No tools, no settings, no MCP servers, our
  system prompt, an empty working directory; it reports the model that answered.
  `$KERNEL_CLAUDE_CMD` replaces the `claude` command (tests use a fake).

Keys come from the environment variable the endpoint names (`key_env`), never from the graph.
"""
import inspect
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

TIMEOUT = 600


class CallFailed(RuntimeError):
    pass


def _key(cfg) -> str | None:
    env = cfg.get("key_env")
    if not env:
        return None
    key = os.environ.get(env)
    if not key:
        raise CallFailed(f"endpoint {cfg['endpoint']} needs its key in ${env}, which is not set")
    return key


def _anthropic(cfg) -> dict:
    import anthropic
    # an endpoint without key_env (a local Messages-compatible server) gets a placeholder key
    client = anthropic.Anthropic(api_key=_key(cfg) or "unused", base_url=cfg.get("base_url"),
                                 timeout=TIMEOUT, max_retries=0)
    # parameters the SDK names go as keywords; any other goes as-is in the request body
    known = set(inspect.signature(client.messages.create).parameters)
    kwargs = {"model": cfg["model_id"], "messages": cfg["messages"], "max_tokens": 1024}
    extra = {}
    for name, v in cfg["params"].items():
        (kwargs if name in known else extra)[name] = v
    if extra:
        kwargs["extra_body"] = extra
    if cfg.get("system"):
        kwargs["system"] = cfg["system"]
    try:
        msg = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise CallFailed(f"HTTP {e.status_code} from {cfg['endpoint']}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise CallFailed(f"connection error to {cfg['endpoint']}: {e}") from e
    u = msg.usage
    return {"text": "".join(b.text for b in msg.content if b.type == "text"),
            "stop_reason": msg.stop_reason, "model": msg.model,
            "usage": {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                      "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None) or 0,
                      "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None) or 0}}


def _openai(cfg) -> dict:
    messages = ([{"role": "system", "content": cfg["system"]}] if cfg.get("system") else []) + cfg["messages"]
    body = json.dumps({"model": cfg["model_id"], "messages": messages, **cfg["params"]}).encode()
    headers = {"Content-Type": "application/json"}
    key = _key(cfg)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions", body, headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raise CallFailed(f"HTTP {e.code} from {cfg['endpoint']}: {e.read()[:300]!r}") from e
    except urllib.error.URLError as e:
        raise CallFailed(f"connection error to {cfg['endpoint']}: {e.reason}") from e
    choice = resp["choices"][0]
    u = resp.get("usage") or {}
    return {"text": choice["message"].get("content") or "", "stop_reason": choice.get("finish_reason"),
            "model": resp.get("model", cfg["model_id"]),
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}


def _litellm(cfg) -> dict:
    # LiteLLM fetches its model price map from the network at import unless told not to; the
    # catalog's prices are what count here
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    try:
        import litellm
        import openai
    except ImportError as e:
        raise CallFailed("the litellm endpoint kind needs the litellm package (pip install 'kernel[litellm]')") from e
    messages = ([{"role": "system", "content": cfg["system"]}] if cfg.get("system") else []) + cfg["messages"]
    try:
        r = litellm.completion(model=cfg["model_id"], messages=messages, api_base=cfg.get("base_url"),
                               api_key=_key(cfg) or "unused", timeout=TIMEOUT, num_retries=0, **cfg["params"])
    except openai.OpenAIError as e:  # LiteLLM's errors derive from the openai package's
        raise CallFailed(f"{type(e).__name__} from {cfg['endpoint']}: {str(e)[:300]}") from e
    choice, u = r.choices[0], r.usage
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) or 0
    return {"text": choice.message.content or "", "stop_reason": choice.finish_reason, "model": r.model,
            "usage": {"input_tokens": (u.prompt_tokens or 0) - cached, "output_tokens": u.completion_tokens or 0,
                      "cache_read_input_tokens": cached,
                      "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None) or 0}}


def cli_usage(out: dict, fallback_model: str) -> tuple[str, dict]:
    """The answering model and usage from a `claude -p` result. `modelUsage` totals every request
    the CLI made (its top-level `usage` can count far fewer input tokens; docs/SPIKE_NOTES.md,
    S3), so tokens are summed from it; the answering model is the one with the most output."""
    by_model = out.get("modelUsage") or {}
    if not by_model:
        u = out.get("usage") or {}
        return fallback_model, {f: u.get(f, 0) for f in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                         "cache_creation_input_tokens")}
    total = lambda f: sum(int(m.get(f, 0) or 0) for m in by_model.values())  # noqa: E731
    return (max(by_model, key=lambda m: by_model[m].get("outputTokens", 0)),
            {"input_tokens": total("inputTokens"), "output_tokens": total("outputTokens"),
             "cache_read_input_tokens": total("cacheReadInputTokens"),
             "cache_creation_input_tokens": total("cacheCreationInputTokens")})


def claude_cmd() -> list[str]:
    return os.environ.get("KERNEL_CLAUDE_CMD", "claude").split()


def _claude_code(cfg) -> dict:
    """`claude -p` with model_id as --model (an alias such as `haiku`, or a full id). One user
    message with text; the CLI takes no sampling parameters, so any parameter is refused."""
    msgs = cfg["messages"]
    if len(msgs) != 1 or msgs[0]["role"] != "user" or not isinstance(msgs[0]["content"], str):
        raise CallFailed("the claude_code endpoint takes exactly one user message with text content")
    if cfg["params"]:
        raise CallFailed(f"the claude_code endpoint takes no parameters, got {sorted(cfg['params'])}")
    argv = [*claude_cmd(), "-p", "--model", cfg["model_id"],
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
            "--system-prompt", cfg.get("system") or "You are a helpful assistant.", "--output-format", "json"]
    with tempfile.TemporaryDirectory(prefix="kernel-claude-") as empty:  # no project files or CLAUDE.md
        try:
            r = subprocess.run(argv, input=msgs[0]["content"], capture_output=True, text=True, cwd=empty,
                               timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise CallFailed("claude -p timed out") from e
        except FileNotFoundError as e:
            raise CallFailed(f"claude -p: {e}") from e
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise CallFailed(f"claude -p exited {r.returncode}: {(r.stderr or r.stdout)[-300:]}") from None
    if out.get("is_error") or r.returncode != 0:
        raise CallFailed(f"claude -p failed: {str(out.get('result') or r.stderr)[-300:]}")
    served, usage = cli_usage(out, cfg["model_id"])
    return {"text": out.get("result") or "", "stop_reason": out.get("stop_reason"), "model": served,
            "usage": usage, "reported_cost_usd": out.get("total_cost_usd")}


APIS = {"anthropic": _anthropic, "openai": _openai, "litellm": _litellm, "claude_code": _claude_code}


def call(cfg) -> dict:
    if cfg.get("api") not in APIS:
        raise CallFailed(f"endpoint {cfg.get('endpoint')} has api {cfg.get('api')!r}; one of {sorted(APIS)}")
    return APIS[cfg["api"]](cfg)

"""The S5 gate (docs/PLAN.md): model routing and agent harnesses.

A `litellm` endpoint reaches an OpenAI-style and an Anthropic-style server (the local fake) and
the call is recorded with tokens, cost and the answering model. An agent's only tool is the
sandbox, and the sandbox server it starts sees none of the caller's environment. In the
entity's terms: Haiku as an agent writes code for a task and tests it in the sandbox by itself;
the kernel's own sandbox run of the task's tests on the final code is observed, the claim
tracing back through that run to the agent run, whose transcript holds the agent's sandbox
calls. A role over budget is refused for agent runs too (model calls and agent runs share it).
With RuleGo built with its AI components, an `ai/llm` chain reaches the fake endpoint.

The agent tests on real Haiku go through `claude -p`; a fake `claude` (tests/fake_claude.py)
shows the flags and the MCP config the harness passes.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import requests
from psycopg.types.json import Jsonb

from conftest import needs_claude
from fake_model import serve
from kernel import claims, config, harnesses, models, modules
from kernel.nodes import agent_run, model_call
from test_sandbox import engine_ready

ROOT = Path(__file__).resolve().parent.parent
CODER = ROOT / "tests" / "modules" / "coder"
ROLE = "coder.agent"
FAKE_CLAUDE = f"{sys.executable} {ROOT / 'tests' / 'fake_claude.py'}"
needs_sandbox = pytest.mark.skipif(not engine_ready(), reason="needs docker with the sandbox image pulled")


@pytest.fixture(scope="module")
def fake():
    with serve() as server:
        yield server


@pytest.fixture(scope="module")
def entity(fresh_core, rulego, fake):
    from kernel import db
    with db.connect() as c:
        modules.install(ROOT / "modules" / "models", c, rulego)
        modules.install(CODER, c, rulego)
    rulego.execute("models_catalog", {})  # the module's own catalog: Haiku through claude -p
    rulego.execute("models_catalog", {"catalog": {
        "endpoints": [{"id": "lite-openai-style", "api": "litellm", "base_url": f"{fake.url}/v1"},
                      {"id": "lite-anthropic-style", "api": "litellm", "base_url": fake.url}],
        "models": [{"id": "lite-o", "endpoint": "lite-openai-style", "model_id": "openai/small-o",
                    "prices": {"input": 1.0, "output": 2.0}},
                   {"id": "lite-a", "endpoint": "lite-anthropic-style", "model_id": "anthropic/small-a",
                    "prices": {"input": 0.5, "output": 1.0}, "params": {"max_tokens": 64}}]},
        "uri": "test:catalog"})
    return rulego


def set_role(rulego, role, **choice):
    rulego.execute("models_set_role", {"role": role, "by": "gate-test", **choice})


def sources(conn, kind, role=ROLE, status=None):
    rows = conn.execute("MATCH (s:source) WHERE s.kind = %s RETURN id(s)::text, properties(s) ORDER BY s.recorded_at",
                        (Jsonb(kind),)).fetchall()
    conn.commit()
    return [{"id": i, "uri": p["uri"], **p["content"]} for i, p in rows
            if p["content"].get("role", role) == role and (status is None or p["content"]["status"] == status)]


@pytest.mark.skipif(importlib.util.find_spec("litellm") is None, reason="needs the litellm extra")
def test_litellm_reaches_openai_and_anthropic_styles(entity, conn, fake):
    try:
        litellm_calls(entity, conn, fake)
    finally:  # the role back on its default model: the fake goes when this module's tests end
        set_role(entity, "coder.writer", model="claude-code-haiku", budget={"tokens": 100000}, params={})


def litellm_calls(entity, conn, fake):
    fake.reply = lambda body: "routed"
    n = len(fake.calls)
    set_role(entity, "coder.writer", model="lite-o", budget={"usd": 1.0}, params={"temperature": 0})
    reply = model_call.run(conn, {"role": "coder.writer", "system": "be brief", "prompt": "hi"}, None)
    req = fake.calls[n]
    assert req["path"] == "/v1/chat/completions" and req["body"]["model"] == "small-o"
    assert req["body"]["temperature"] == 0 and req["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    last = sources(conn, "model_call", "coder.writer", "ok")[-1]
    assert reply["text"] == "routed" and last["api"] == "litellm" and last["endpoint"] == "lite-openai-style"
    assert (last["served_by"], last["tokens"], last["cost_usd"]) == ("small-o", 1500, 0.002)

    set_role(entity, "coder.writer", model="lite-a", budget={"usd": 1.0}, params={})
    model_call.run(conn, {"role": "coder.writer", "system": "be brief", "prompt": "hi"}, None)
    req = fake.calls[n + 1]
    assert req["path"] == "/v1/messages" and req["body"]["model"] == "small-a" and req["body"]["max_tokens"] == 64
    last = sources(conn, "model_call", "coder.writer", "ok")[-1]
    assert last["endpoint"] == "lite-anthropic-style" and last["served_by"] == "small-a"
    assert last["cost_usd"] == pytest.approx((1000 * 0.5 + 500 * 1.0) / 1e6)


def test_the_agents_only_tool_is_the_sandbox(entity, conn, monkeypatch):
    """What the harness runs `claude -p` with (a fake `claude` that echoes its arguments)."""
    monkeypatch.setenv("KERNEL_CLAUDE_CMD", FAKE_CLAUDE)
    set_role(entity, ROLE, model="claude-code-haiku", budget={"tokens": 100000}, params={})
    out = agent_run.run(conn, {"role": ROLE, "task": "do it", "max_turns": 50}, None)
    seen = json.loads(out["text"])
    argv = seen["argv"]
    assert seen["prompt"] == "do it" and not seen["cwd"].startswith(str(ROOT))
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv  # no built-in tools, no others
    assert argv[argv.index("--setting-sources") + 1] == "" and "--no-session-persistence" in argv
    assert argv[argv.index("--max-turns") + 1] == str(config.load()["agents"]["max_turns"])  # capped by config
    assert argv[argv.index("--allowedTools") + 1] == "mcp__sandbox"
    [(name, server)] = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"].items()
    assert name == "sandbox" and server["command"] == "env" and server["args"][0] == "-i"
    env = {a.split("=", 1)[0] for a in server["args"][1:] if "=" in a and not a.startswith("/")}
    assert env == {"PATH", "HOME", "PYTHONPATH"}
    assert server["args"][len(env) + 1:][-4:] == ["-m", "sandbox.server", "--config", config.load()["file"]]

    [run] = [s for s in sources(conn, "agent_run", status="ok") if s["id"] == out["source"]]
    assert run["uri"] == "agent:claude-code-haiku" and run["served_by"] == "fake-served-model"
    assert run["tools_given"] == ["mcp__sandbox__sandbox_run"] and run["tool_calls"] == 1
    assert [t["type"] for t in run["transcript"]] == ["text", "tool_call", "tool_result", "text"]
    assert run["tokens"] == 1311 and run["reported_cost_usd"] == 0.0004 and run["request"]["tools"] == ["sandbox"]


@needs_claude
def test_the_sandbox_server_sees_none_of_our_environment(entity, conn, tmp_path, monkeypatch):
    """Claude Code starts MCP servers with its whole environment; ours is started through
    `env -i`. A probe server in the sandbox server's place records what it was given."""
    seen = tmp_path / "env.json"
    probe = tmp_path / "probe.py"
    probe.write_text(f"""import json, os
open({str(seen)!r}, "w").write(json.dumps(sorted(os.environ)))
from mcp.server import MCPServer
s = MCPServer("probe")
@s.tool()
def ping() -> str:
    return "pong"
s.run()
""")
    cfg = config.load()
    cfg["sandbox"]["server"] = {"command": [sys.executable, str(probe)]}
    monkeypatch.setenv("SANDBOX_TEST_SECRET", "must-not-leak")
    set_role(entity, ROLE, model="claude-code-haiku", budget={"tokens": 100000}, params={})
    out = agent_run.run(conn, {"role": ROLE, "task": "Reply with the word ok.", "max_turns": 2}, None, cfg=cfg)
    assert json.loads(seen.read_text()) and set(json.loads(seen.read_text())) <= {"PATH", "HOME", "PYTHONPATH",
                                                                                  "LC_CTYPE"}
    run = [s for s in sources(conn, "agent_run") if s["id"] == out["source"]][0]
    assert run["tools_given"] == ["mcp__sandbox__ping"] and run["served_by"].startswith("claude-haiku")


@needs_claude
@needs_sandbox
def test_haiku_as_an_agent_writes_and_tests_code(entity, conn):
    """In the entity's terms: the agent tests its own code in the sandbox; the kernel tests the
    final code again in its own recorded run, and the observed result traces back to the agent."""
    set_role(entity, ROLE, model="claude-code-haiku", budget={"tokens": 300000}, params={})
    task = {"task": "run_lengths",
            "spec": "Write solution.py defining run_lengths(s: str) -> list[tuple[str, int]], the run-length "
                    "encoding of s, e.g. run_lengths('aaabcc') == [('a', 3), ('b', 1), ('c', 2)]; '' gives [].",
            "tests": "from solution import run_lengths as r\nassert r('aaabcc') == [('a', 3), ('b', 1), ('c', 2)]\n"
                     "assert r('') == []\nassert r('z') == [('z', 1)]\nassert r('aab' * 2)[2] == ('a', 2)\n"
                     "print('passed')\n"}
    out = entity.execute("coder_agent", task)
    assert out["sandbox_result"]["stdout"].strip() == "passed", out["sandbox_result"]

    [passed] = claims.held(conn, out["written"]["things"]["task"], "coder.passed")
    conn.commit()
    w = claims.why(conn, passed["id"])
    assert passed["value"] is True and passed["method"] == "observed"
    check = w["source"]  # the kernel's own run of the task's tests
    assert check["kind"] == "sandbox_run" and check["content"]["result"]["isolated"] is True
    [run] = [s for s in sources(conn, "agent_run") if s["id"] == check["content"]["inputs"]["agent_run"]]
    assert run["status"] == "ok" and run["served_by"].startswith("claude-haiku") and run["tokens"] > 0
    assert run["tools_given"] == ["mcp__sandbox__sandbox_run"]  # nothing else: no shell, files or web
    calls = [t for t in run["transcript"] if t["type"] == "tool_call"]
    assert calls and all(c["name"] == "mcp__sandbox__sandbox_run" for c in calls)  # it tested its code
    results = [t for t in run["transcript"] if t["type"] == "tool_result"]
    assert results and all('"isolated": true' in r["content"] for r in results if not r["is_error"])
    assert check["content"]["request"]["files"]["solution.py"] in run["reply"]["text"]


def test_model_calls_and_agent_runs_share_a_budget(entity, conn, monkeypatch):
    monkeypatch.setenv("KERNEL_CLAUDE_CMD", FAKE_CLAUDE)
    set_role(entity, ROLE, model="claude-code-haiku", budget={"tokens": 2000}, params={})  # counts from now
    model_call.run(conn, {"role": ROLE, "prompt": "one-shot"}, None)  # 1311 tokens
    agent_run.run(conn, {"role": ROLE, "task": "agent"}, None)  # 1311 < 2000: allowed
    with pytest.raises(models.OverBudget, match="spent 2622 of its 2000 tokens"):
        agent_run.run(conn, {"role": ROLE, "task": "one too many", "inputs": {"why": "test"}}, None)
    refused = sources(conn, "agent_run", status="refused")[-1]
    assert refused["inputs"] == {"why": "test"} and "spent 2622 of its 2000 tokens" in refused["error"]


def test_a_failed_agent_run_is_recorded(entity, conn, monkeypatch):
    monkeypatch.setenv("KERNEL_CLAUDE_CMD", "false")  # exits 1, says nothing
    set_role(entity, ROLE, model="claude-code-haiku", budget={"tokens": 100000}, params={})
    with pytest.raises(harnesses.Failed, match="without a result"):
        agent_run.run(conn, {"role": ROLE, "task": "anything"}, None)
    failed = sources(conn, "agent_run", status="failed")[-1]
    assert failed["request"]["task"] == "anything" and failed["transcript"] == []


def ai_build(rulego) -> bool:
    try:
        return '"ai/llm"' in requests.get(f"{rulego.url}/api/v1/components", timeout=5).text
    except requests.RequestException:
        return False


def test_rulego_ai_llm_reaches_the_fake(entity, fake):
    """With the AI build (KERNEL_RULEGO_AI=1 tools/dev.sh up): ai/llm works, and returns only the
    reply text: no tokens, no answering model (why recorded calls stay with model_call)."""
    if not ai_build(entity):
        pytest.skip("RuleGo is not the AI build (KERNEL_RULEGO_AI=1 tools/dev.sh up)")
    fake.reply = lambda body: "from ai/llm"
    entity.deploy({"ruleChain": {"id": "test_ai_llm", "name": "test: ai/llm", "root": True},
                   "metadata": {"nodes": [{"id": "llm", "type": "ai/llm", "configuration": {
                       "url": f"{fake.url}/v1", "key": "unused", "model": "small-o",
                       "messages": [{"role": "user", "content": "${msg.text}"}]}}], "connections": []}})
    n = len(fake.calls)
    assert entity.execute("test_ai_llm", {"text": "hello"}) == "from ai/llm"
    assert fake.calls[n]["body"]["messages"] == [{"role": "user", "content": "hello"}]

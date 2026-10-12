"""The S3 gate (docs/PLAN.md): model access from the graph.

A person's sentence becomes an inferred setpoint through a model call resolved from the graph,
and the comfort belief derived from it traces back through the call to the words. Swapping the
role's model changes the endpoint called; a role over budget is refused; tokens, cost and the
answering model are in the graph.

The entity's own model is real: Haiku through `claude -p` on the logged-in Claude Code session
(the role's default), so these tests make a few small calls. A local fake endpoint
(tests/fake_model.py) stands in for the `anthropic` and `openai` API kinds, which need a key or
a server this environment doesn't have, and a fake `claude` (tests/fake_claude.py) shows the
flags the CLI is run with.
"""
import json
import sys
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from conftest import needs_claude, wait_for
from fake_model import serve
from kernel import claims, models, modules
from kernel.nodes import model_call
from kernel.rulego import ChainFailed

ROOT = Path(__file__).resolve().parent.parent
ROLE = "climate_chat.interpreter"
SENTENCE = "It's freezing in the study, could you make it 22 degrees?"


@pytest.fixture(scope="module")
def fake():
    with serve() as server:
        yield server


@pytest.fixture(scope="module")
def entity(fresh_core, rulego, fake):
    from kernel import db
    with db.connect() as c:
        for m in ("models", "climate_sense", "climate_comfort", "climate_chat"):
            modules.install(ROOT / "modules" / m, c, rulego)
    rulego.execute("models_catalog", {"catalog": catalog_for(fake.url), "uri": "test:catalog"})
    return rulego


def catalog_for(base_url, small_a_input=0.5):
    return {"endpoints": [{"id": "fake-anthropic", "api": "anthropic", "base_url": base_url},
                          {"id": "fake-openai", "api": "openai", "base_url": f"{base_url}/v1"},
                          {"id": "dead", "api": "openai", "base_url": "http://127.0.0.1:9/v1"}],
            "models": [{"id": "small-a", "endpoint": "fake-anthropic", "prices": {"input": small_a_input, "output": 1.0},
                        "params": {"max_tokens": 64}},
                       {"id": "small-o", "endpoint": "fake-openai", "prices": {"input": 1.0, "output": 2.0}},
                       {"id": "dead-model", "endpoint": "dead"}]}




def thing(conn, label, key):
    row = conn.execute(f"MATCH (t:{label}) WHERE t.key = %s RETURN id(t)::text", (Jsonb(key),)).fetchone()
    conn.commit()
    return row[0]


def held(conn, label, key, predicate):
    out = claims.held(conn, thing(conn, label, key), predicate)
    conn.commit()
    return out


def calls(conn, status=None):
    """model_call sources for the chat role, oldest first."""
    rows = conn.execute("""MATCH (s:source) WHERE s.kind = 'model_call' RETURN id(s)::text, properties(s)
                           ORDER BY s.recorded_at""").fetchall()
    conn.commit()
    return [{"id": i, **p["content"]} for i, p in rows
            if p["content"]["role"] == ROLE and (status is None or p["content"]["status"] == status)]


def set_role(rulego, **choice):
    rulego.execute("models_set_role", {"role": ROLE, "by": "gate-test", **choice})


def reading(rulego, room, celsius, seq):
    rulego.execute("climate_sense_reading", {"sensor": f"{room}-thermometer", "room": room, "celsius": celsius,
                                             "heater": False, "at": "2026-10-12T09:00:00.000000Z", "seq": seq})


def test_catalog_is_stated_with_spans(entity, conn):
    assert entity.execute("models_catalog", {})["changed"] > 0  # the module's own catalog.yaml
    assert entity.execute("models_catalog", {})["changed"] == 0  # unchanged: nothing written
    [prices] = held(conn, "model", "claude-haiku-5-5", "models.prices")
    w = claims.why(conn, prices["id"])
    assert prices["value"] == {"input": 0.10, "output": 0.50} and prices["method"] == "stated"
    assert w["span"] == "$.models[0].prices" and w["source"]["uri"] == "file:catalog.yaml"
    assert w["source"]["content"]["models"][0]["prices"] == prices["value"]
    [api] = held(conn, "endpoint", "claude-code", "models.api")
    assert api["value"] == "claude_code"

    # a changed price supersedes; the unchanged fields of the entry write nothing
    [old] = held(conn, "model", "small-a", "models.prices")
    out = entity.execute("models_catalog", {"catalog": catalog_for(entity_url(conn), 0.75), "uri": "test:catalog"})
    assert out["changed"] == 1
    [new] = held(conn, "model", "small-a", "models.prices")
    assert new["value"]["input"] == 0.75 and claims.why(conn, new["id"])["supersedes"] == old["id"]
    entity.execute("models_catalog", {"catalog": catalog_for(entity_url(conn)), "uri": "test:catalog"})


def entity_url(conn):
    [u] = held(conn, "endpoint", "fake-anthropic", "models.base_url")
    return u["value"]


def test_roles_are_defaulted_from_the_manifest(entity, conn):
    [uses] = held(conn, "model_role", ROLE, "models.uses")
    assert uses["method"] == "defaulted" and uses["object"] == thing(conn, "model", "claude-code-haiku")
    assert claims.why(conn, uses["id"])["span"] == "$.models.interpreter.model"
    [budget] = held(conn, "model_role", ROLE, "models.budget")
    assert budget["value"] == {"tokens": 200000}
    cfg = models.resolve(conn, ROLE)
    conn.commit()
    assert (cfg["api"], cfg["model_id"], cfg["endpoint"]) == ("claude_code", "haiku", "claude-code")


@needs_claude
def test_words_become_an_inferred_setpoint_through_haiku(entity, conn, bridge):
    """The gate, on the role's default model (Haiku through `claude -p`): through the call, back
    to the words."""
    bridge()
    [uses] = held(conn, "model_role", ROLE, "models.uses")
    assert uses["method"] == "defaulted"
    reading(entity, "study", 19.0, 1)

    out = entity.execute("climate_chat_say", {"text": SENTENCE, "by": "gate-test"})
    assert out["understood"] is True, out["model_reply"]["text"]

    comfort = wait_for(lambda: held(conn, "room", "study", "climate.comfort"), timeout=15)
    assert [c["value"] for c in comfort] == ["cold"]
    t = claims.trace(conn, comfort[-1]["id"])
    [setp] = [d for d in t["derived_from"] if d["predicate"] == "climate.setpoint"]
    assert setp["value"] == 22 and setp["method"] == "inferred" and setp["confidence"] == 0.8
    assert setp["agent"] == "module:climate_chat" and setp["span"] == "$.reply.text"
    call = setp["source"]
    assert call["kind"] == "model_call" and call["uri"] == "model:claude-code-haiku"
    c = call["content"]
    assert c["inputs"]["message"] == {"text": SENTENCE, "by": "gate-test"}  # the words
    assert SENTENCE in c["request"]["messages"][0]["content"]
    assert c["status"] == "ok" and c["served_by"].startswith("claude-haiku") and c["endpoint"] == "claude-code"
    assert c["tokens"] > 0 and c["usage"]["output_tokens"] > 0
    assert c["cost_usd"] == 0 and c["reported_cost_usd"] > 0  # the subscription; the CLI's list-price figure
    assert uses["id"] in c["resolved_from"]  # which model was used, and why: the module's default


@needs_claude
def test_haiku_leaves_other_talk_alone(entity, conn):
    before = held(conn, "room", "study", "climate.setpoint")
    out = entity.execute("climate_chat_say", {"text": "What a lovely sunset tonight.", "by": "gate-test"})
    assert out["understood"] is False, out["model_reply"]["text"]
    assert held(conn, "room", "study", "climate.setpoint") == before
    assert calls(conn, "ok")[-1]["inputs"]["message"]["text"] == "What a lovely sunset tonight."  # still recorded


def test_swapping_the_model_changes_the_endpoint(entity, conn, fake):
    """The `anthropic` and `openai` API kinds, on the local fake (no API key in this environment)."""
    set_role(entity, model="small-a", budget={"usd": 1.0})
    [uses] = held(conn, "model_role", ROLE, "models.uses")
    assert uses["method"] == "stated" and claims.why(conn, uses["id"])["supersedes"] is not None
    fake.reply = lambda body: '{"room": "study", "celsius": 23}'
    n = len(fake.calls)
    entity.execute("climate_chat_say", {"text": "23 in the study please", "by": "gate-test"})
    [req] = fake.calls[n:]
    assert req["path"] == "/v1/messages" and req["body"]["model"] == "small-a" and req["body"]["max_tokens"] == 64
    last = calls(conn, "ok")[-1]
    assert (last["endpoint"], last["served_by"], last["tokens"]) == ("fake-anthropic", "small-a", 1500)
    assert last["cost_usd"] == pytest.approx((1000 * 0.5 + 500 * 1.0) / 1e6) and uses["id"] in last["resolved_from"]

    set_role(entity, model="small-o", params={"temperature": 0})
    fake.reply = lambda body: '{"room": "study", "celsius": 21}'
    entity.execute("climate_chat_say", {"text": "21 in the study please", "by": "gate-test"})
    [req] = fake.calls[n + 1:]
    assert req["path"] == "/v1/chat/completions" and req["body"]["model"] == "small-o"
    assert req["body"]["temperature"] == 0 and req["body"]["messages"][0]["role"] == "system"
    last = calls(conn, "ok")[-1]
    assert (last["endpoint"], last["api"], last["cost_usd"]) == ("fake-openai", "openai", 0.002)
    assert held(conn, "room", "study", "climate.setpoint")[-1]["value"] == 21


@needs_claude
def test_a_role_over_budget_is_refused(entity, conn):
    set_role(entity, model="claude-code-haiku", budget={"tokens": 1}, params={})  # a new budget counts from now
    entity.execute("climate_chat_say", {"text": "hello", "by": "gate-test"})  # spent 0 < 1: allowed
    spent = calls(conn, "ok")[-1]["tokens"]
    with pytest.raises(ChainFailed, match="budget"):
        entity.execute("climate_chat_say", {"text": "one too many", "by": "gate-test"})
    refused = calls(conn, "refused")[-1]
    assert refused["inputs"]["message"]["text"] == "one too many"
    assert f"spent {spent} of its 1 tokens budget" in refused["error"]
    assert models.spent(conn, ROLE, held(conn, "model_role", ROLE, "models.budget")[-1]["recorded_at"])["calls"] == 1
    conn.commit()


def test_a_failed_call_is_recorded(entity, conn):
    set_role(entity, model="dead-model", budget={"usd": 1.0})
    before = held(conn, "room", "study", "climate.setpoint")
    with pytest.raises(ChainFailed, match="connection"):
        entity.execute("climate_chat_say", {"text": "make the study 25", "by": "gate-test"})
    failed = calls(conn, "failed")[-1]
    assert failed["endpoint"] == "dead" and "connection" in failed["error"]
    assert held(conn, "room", "study", "climate.setpoint") == before  # nothing inferred


def test_claude_code_flags(entity, conn, monkeypatch):
    """What `claude -p` is run with: no tools, settings or MCP servers, our system prompt, an empty
    directory; tokens summed over every model the CLI used. (A fake `claude` that echoes its
    arguments, run in-process: the RuleGo server's environment can't point at it.)"""
    monkeypatch.setenv("KERNEL_CLAUDE_CMD", f"{sys.executable} {ROOT / 'tests' / 'fake_claude.py'}")
    set_role(entity, model="claude-code-haiku", budget={"tokens": 5000}, params={})
    reply = model_call.run(conn, {"role": ROLE, "system": "be brief", "prompt": "hi"}, None)
    seen = json.loads(reply["text"])
    argv = seen["argv"]
    assert seen["prompt"] == "hi" and not seen["cwd"].startswith(str(ROOT))
    assert argv[argv.index("--model") + 1] == "haiku" and argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == "be brief" and argv[argv.index("--setting-sources") + 1] == ""
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(argv)
    c = calls(conn, "ok")[-1]
    assert c["served_by"] == "fake-served-model" and c["cost_usd"] == 0 and c["reported_cost_usd"] == 0.0004
    assert c["tokens"] == 1311
    for _ in range(3):  # 1311 tokens a call against 5000
        model_call.run(conn, {"role": ROLE, "prompt": "again"}, None)
    with pytest.raises(models.OverBudget, match="tokens"):
        model_call.run(conn, {"role": ROLE, "prompt": "over"}, None)

"""M4 gate: models known to the kernel, the budgeted model_call sub-chain, runs as sources.

The sub-chain's three steps run through pclib.node.call exactly as RuleGo's x/python node
calls them, against a local fake endpoint that speaks both the Anthropic Messages API and the
OpenAI-compatible chat API. tests/smoke runs the same chain in RuleGo.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from kernel import Kernel
from modules.evaluation.chains import build as chains
from modules.evaluation.models import (CATALOG, BudgetExceeded, cost_usd, load_catalog, model_iri,
                                       record_spend, resolve, use_model)
from modules.systems.runs import record_run, start_run
from pclib.node import call as node_call
from tests.fake_endpoint import catalog as fake_catalog
from tests.fake_endpoint import serve

ROOT = Path(__file__).resolve().parents[2]
STEPS = {n["id"]: str(ROOT / n["_pclib"]["script"]) for n in chains.model_call()["metadata"]["nodes"]}

@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("FAKE_KEY", "test-key")
    with serve() as (base, calls):
        yield base, calls


@pytest.fixture
def k(db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("PC_DATA", str(tmp_path / "data"))
    kern = Kernel.connect(db_url, data_root=tmp_path / "data")
    kern.load_module("evaluation")
    yield kern
    kern.conn.close()


@pytest.fixture
def world(k, endpoint, tmp_path):
    """A catalog with two fake models (one per API kind) and a system that uses one."""
    base, calls = endpoint
    tag = uuid.uuid4().hex[:6]
    catalog = tmp_path / "models.yaml"
    catalog.write_text(fake_catalog(tag, base))
    me = k.ensure_node("agent", f"urn:test:{tag}:me")
    load_catalog(k, catalog, by=me)
    system = k.create_context("system", "caller", iri=f"urn:test:{tag}:system")
    uses = use_model(k, system, "actor", f"small-a-{tag}", by=me, params={"temperature": 0})

    class W:
        pass
    W.tag, W.me, W.system, W.uses, W.calls, W.catalog = tag, me, system, uses, calls, catalog
    return W


def model_call(run_id, name, data, task="t1"):
    """The model_call sub-chain as RuleGo runs it: three x/python nodes, metadata flowing on."""
    msg, meta = data, {"run_id": run_id, "task_id": task, "episode": "0", "call": name}
    for node in ("resolve", "invoke", "account"):
        out = node_call(STEPS[node], node, msg, meta)
        msg, meta = out["msg"], out["metadata"]
    return msg, meta


def ask(text):
    return {"model_role": "actor", "messages": [{"role": "user", "content": text}]}


def run_row(conn, run_id):
    return conn.execute("select spent_usd, budget_usd from run.run where id = %s", (run_id,)).fetchone()


def test_catalog_is_evidenced_and_idempotent(k):
    me = k.ensure_node("agent", "urn:test:catalog-loader")
    sha = load_catalog(k, CATALOG, by=me)
    assert load_catalog(k, CATALOG, by=me) == sha
    [price] = [a for a in k.query(subject=model_iri("claude-haiku-5-5"), predicate="ev:price")
               if a.args == {"per": "input"}]
    assert price.value == 0.10 and price.method == "stated"
    [ev] = k.why(price.id)["evidence"]
    lines = CATALOG.read_text().splitlines()[ev["locator"]["line_start"] - 1:ev["locator"]["line_end"]]
    assert ev["source"]["sha256"] == sha and lines[0].strip() == "- id: claude-haiku-5-5"
    assert any("prices:" in ln for ln in lines) and not lines[-1].strip() == ""


def test_changed_catalog_supersedes(k, world):
    text = world.catalog.read_text().replace("input: 1.0, output: 2.0", "input: 3.0, output: 2.0")
    world.catalog.write_text(text)
    load_catalog(k, world.catalog, by=world.me)
    rows = k.query(subject=model_iri(f"small-a-{world.tag}"), predicate="ev:price", status=("accepted", "superseded"))
    by_status = {(a.args["per"], a.status): a.value for a in rows}
    assert by_status[("input", "accepted")] == 3.0 and by_status[("input", "superseded")] == 1.0
    assert by_status[("output", "accepted")] == 2.0 and ("output", "superseded") not in by_status


def test_resolve_layers_parameters(k, world):
    cfg = resolve(k, world.system, "actor", {"max_tokens": 32})
    assert cfg["model_id"] == f"small-a-{world.tag}" and cfg["api"] == "anthropic"
    assert cfg["key_env"] == "FAKE_KEY" and cfg["prices"] == {"input": 1.0, "output": 2.0}
    assert cfg["params"] == {"max_tokens": 32, "temperature": 0}
    with pytest.raises(LookupError, match="no model for role 'critic'"):
        resolve(k, world.system, "critic")


def test_budget_refuses_once_spent(k, world, conn):
    run = start_run(k, world.system, budget_usd=0.005, by=world.me)
    per_call = cost_usd({"input": 1.0, "output": 2.0}, {"input_tokens": 1000, "output_tokens": 500})
    assert per_call == 0.002
    for i in range(3):
        out, meta = model_call(run, f"c{i}", ask(f"question {i}"))
        assert out["text"] == f"reply {i + 1}" and out["cost_usd"] == per_call and meta["call"] == ""
    with pytest.raises(BudgetExceeded, match="refusing model call 'c3'"):
        model_call(run, "c3", ask("one too many"))
    assert len(world.calls) == 3
    assert world.calls[0]["auth"] == "test-key" and world.calls[0]["body"]["temperature"] == 0
    assert float(run_row(conn, run)[0]) == pytest.approx(0.006)
    events = conn.execute("select event, slot, payload from run.trace where run_id = %s order by id", (run,)).fetchall()
    assert [e for e, _, _ in events] == ["llm.request", "llm.response"] * 3
    assert events[0][1] == "c0" and events[0][2]["messages"][0]["content"] == "question 0"
    assert events[1][2]["usage"]["input_tokens"] == 1000

    model_call(run, "c0", ask("question 0"))                   # a rerun replays, no new call or spend
    assert len(world.calls) == 3 and float(run_row(conn, run)[0]) == pytest.approx(0.006)


def test_call_name_is_required(k, world):
    run = start_run(k, world.system, budget_usd=1, by=world.me)
    with pytest.raises(ValueError, match="set meta.call"):
        model_call(run, "", ask("hi"))


def test_swapping_the_model_is_an_assertion(k, world, conn):
    run = start_run(k, world.system, budget_usd=1, by=world.me)
    model_call(run, "before", ask("hi"))
    k.supersede(world.uses, asserted_by=world.me, reason="try the compatible endpoint",
                object=k.node_id(model_iri(f"small-o-{world.tag}")))
    out, _ = model_call(run, "after", ask("hi again"))
    assert out["text"] == "compat reply"
    assert [c["path"] for c in world.calls] == ["/v1/messages", "/v1/chat/completions"]
    assert world.calls[1]["body"]["model"] == f"small-o-{world.tag}"
    assert world.calls[1]["body"]["temperature"] == 0           # the system's parameter still applies
    assert float(run_row(conn, run)[0]) == pytest.approx(0.002 + 0.001)


def test_run_trace_is_a_source_with_observed_spend(k, world):
    run = start_run(k, world.system, budget_usd=1, by=world.me)
    model_call(run, "a", ask("one"))
    model_call(run, "b", ask("two"))
    runtime = k.ensure_node("agent", "urn:pc:agent:runtime", "runtime")
    recorded = record_run(k, run, by=runtime)
    trace_file = k.data_root / "runs" / run / "trace.jsonl"
    assert len(trace_file.read_text().splitlines()) == 4
    metrics = {k.get(i).args["name"]: k.get(i) for i in record_spend(k, run, recorded, by=runtime)}
    calls_id, spend_id = metrics["llm_calls"].id, metrics["llm_spend_usd"].id
    assert k.get(calls_id).value == 2 and k.get(spend_id).value == pytest.approx(0.004)
    assert metrics["llm_input_tokens"].value == 2000 and metrics["llm_output_tokens"].value == 1000
    assert metrics["llm_served_models"].value == {f"small-a-{world.tag}": 2}
    why = k.why(spend_id)
    assert why["method"] == "observed" and why["context"] == f"urn:pc:run:{run}"
    lines = trace_file.read_text().splitlines()
    for ev in why["evidence"]:
        assert ev["source"]["sha256"] == recorded["sha256"]
        assert ev["source"]["produced_by"] == [{"agent": "urn:pc:agent:runtime", "as": "recorder",
                                                "method": "defaulted"}]
        assert json.loads(lines[ev["locator"]["line_start"] - 1])["event"] == "llm.response"


def test_claude_code_endpoint(k, world, monkeypatch, conn):
    """The claude_code API kind: `claude -p` with no tools or settings, in an empty directory;
    unpriced, with tokens and the answering model recorded."""
    monkeypatch.setenv("PC_CLAUDE_CMD", f"{sys.executable} {ROOT / 'tests/fake_claude.py'}")
    (world.catalog.parent / "cc.yaml").write_text(
        f"endpoints:\n  - {{id: cc-{world.tag}, api: claude_code}}\n"
        f"models:\n  - {{id: cc-haiku-{world.tag}, endpoint: cc-{world.tag}, model_id: haiku}}\n")
    load_catalog(k, world.catalog.parent / "cc.yaml", by=world.me)
    system = k.create_context("system", "subscription caller")
    use_model(k, system, "actor", f"cc-haiku-{world.tag}", by=world.me)
    run = start_run(k, system, budget_usd=1, by=world.me)
    out, _ = model_call(run, "cc", ask("write add()"))
    seen = json.loads(out["text"])
    assert seen["prompt"] == "write add()" and not seen["cwd"].startswith(str(ROOT))
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "haiku" and argv[argv.index("--tools") + 1] == ""
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(argv)
    assert out["model"] == "fake-served-model" and out["cost_usd"] == 0
    assert out["usage"]["output_tokens"] == 7 and out["usage"]["cache_creation_input_tokens"] == 1200
    with pytest.raises(Exception, match="usage limit"):
        model_call(run, "cc2", ask("FAIL please"))


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_real_model_call(k):
    me = k.ensure_node("agent", "urn:test:live")
    load_catalog(k, CATALOG, by=me)
    system = k.create_context("system", "live call")
    use_model(k, system, "actor", "claude-haiku-5-5", by=me, params={"max_tokens": 16})
    run = start_run(k, system, budget_usd=0.01, by=me)
    out, _ = model_call(run, "live", ask("Reply with the single word OK."))
    assert out["text"] and out["usage"]["input_tokens"] > 0 and 0 < out["cost_usd"] < 0.01


def test_committed_chain_is_current(tmp_path):
    """modules/evaluation/chains/*.json and stubs are what build.py generates."""
    here = ROOT / "modules/evaluation/chains"
    before = {p: p.read_bytes() for p in here.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    subprocess.run([sys.executable, "-m", "modules.evaluation.chains.build"], cwd=ROOT, check=True,
                   capture_output=True)
    after = {p: p.read_bytes() for p in here.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert after == before, "run: python -m modules.evaluation.chains.build"

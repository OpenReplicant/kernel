"""M4 in RuleGo: the model_call sub-chain reached through `flow` from a caller chain, against
the fake endpoint in tests/fake_endpoint.py.

Needs RULEGO_URL and RULEGO_DATABASE_URL (as tests/smoke/test_rulego_spike.py), and a
container that can reach this host's 127.0.0.1 (`--network host`) with FAKE_KEY set.
"""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from kernel import Kernel
from modules.evaluation.models import load_catalog, use_model
from modules.systems.runs import record_run, start_run
from pclib import chains as c
from pclib.rulego import ChainFailed, RuleGo
from tests.fake_endpoint import catalog, serve

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("RULEGO_URL")
DB = os.environ.get("RULEGO_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (URL and DB), reason="RULEGO_URL and RULEGO_DATABASE_URL not set")


def test_model_call_subchain_in_rulego(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    rg = RuleGo(URL)
    rg.deploy_paths(ROOT / "modules/evaluation/chains")
    rg.deploy(c.chain("m4_caller", root=True, nodes=[c.subchain("ask", "model_call")]))
    tag = uuid.uuid4().hex[:6]
    with serve() as (base, calls):
        k = Kernel.connect(DB, data_root=tmp_path)
        k.load_module("evaluation")
        me = k.ensure_node("agent", f"urn:smoke:{tag}:me")
        (tmp_path / "models.yaml").write_text(catalog(tag, base))
        load_catalog(k, tmp_path / "models.yaml", by=me)
        system = k.create_context("system", "smoke caller", iri=f"urn:smoke:{tag}:system")
        use_model(k, system, "actor", f"small-a-{tag}", by=me)
        run = start_run(k, system, budget_usd=0.003, by=me)
        meta = {"run_id": run, "task_id": "T", "episode": 0}
        ask = {"model_role": "actor", "messages": [{"role": "user", "content": "hello"}]}

        first = rg.execute("m4_caller", ask, {**meta, "call": "one"}, timeout=60)
        assert first["text"] == "reply 1" and first["cost_usd"] == 0.002
        assert rg.execute("m4_caller", ask, {**meta, "call": "one"}, timeout=60) == first   # replayed
        rg.execute("m4_caller", ask, {**meta, "call": "two"}, timeout=60)
        with pytest.raises(ChainFailed, match="refusing model call 'three'"):
            rg.execute("m4_caller", ask, {**meta, "call": "three"}, timeout=60)
        assert len(calls) == 2

        with psycopg.connect(DB, autocommit=True) as conn:
            spent = conn.execute("select spent_usd from run.run where id = %s", (run,)).fetchone()[0]
            keys = [r[0] for r in conn.execute("select step_key from run.step where run_id = %s order by step_key",
                                                (run,))]
        assert float(spent) == pytest.approx(0.004)
        assert "task=T/ep=0/call=one/invoke" in keys and "task=T/ep=0/call=two/account" in keys
        recorded = record_run(k, run, by=me)
        assert [e["event"] for e in recorded["events"]] == ["llm.request", "llm.response"] * 2
        k.conn.close()

"""M5 in RuleGo: component graphs compiled from the kernel run on stub components.

Reflexion (its spec.yaml through the agent-spec view) loops until its evaluator passes;
swapping the component that fills the memory role (one superseded k:plays) changes what
runs; the thermostat compiles and runs with the same compiler. Needs RULEGO_URL and
RULEGO_DATABASE_URL (see tests/smoke/test_rulego_spike.py).
"""
import os
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest

from kernel import Kernel
from modules.systems.graph import read_graph
from modules.systems.runs import start_run
from pclib import chains as c
from pclib.compile import compile_graph, initial_message
from pclib.rulego import RuleGo
from tests import graphs

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("RULEGO_URL")
DB = os.environ.get("RULEGO_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (URL and DB), reason="RULEGO_URL and RULEGO_DATABASE_URL not set")


@pytest.fixture
def k(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    kern = Kernel.connect(DB, data_root=tmp_path)
    kern.load_module("paper_compiler")
    kern.load_vocabulary(ROOT / "tests/modules/control_loop.yaml")
    yield kern
    kern.conn.close()


@pytest.fixture
def out_dir():
    d = ROOT / "data" / "compiled" / uuid.uuid4().hex[:8]       # inside the repo, so the container sees it
    yield d
    shutil.rmtree(d, ignore_errors=True)


def compile_and_run(k, system, by, out_dir, name, task, budget=1.0):
    g = read_graph(k, system)
    rg = RuleGo(URL)
    for chain in compile_graph(g, name):
        rg.deploy_paths(c.write(chain, out_dir))
    run = start_run(k, system, budget, by)
    meta = {"run_id": run, "task_id": "T1", "episode": 0}
    return run, rg.execute(name, initial_message(g, task), meta, timeout=120), (rg, g, meta)


def trace(run):
    with psycopg.connect(DB) as conn:
        return conn.execute("select episode, event, slot, port, payload from run.trace where run_id = %s order by id",
                            (run,)).fetchall()


def test_reflexion_runs_until_it_passes_and_replays(k, out_dir):
    system, by = graphs.reflexion(k)
    run, out, (rg, g, meta) = compile_and_run(k, system, by, out_dir, f"rx_{out_dir.name}", {"problem": "f"})
    assert out["ctl"]["exit"] == "episode_eval.pass" and out["ctl"]["iter_loop_inject_reflections"] == 3
    emits = [(ep, slot, port) for ep, ev, slot, port, _ in trace(run) if ev == "slot.emit"]
    assert ("test_gen" in {s for _, s, _ in emits}) and emits[0][1] == "test_gen"
    assert [(ep, p) for ep, s, p in emits if s == "episode_eval"] == [(1, "fail"), (2, "fail"), (3, "pass")]
    assert [ep for ep, s, p in emits if s == "reflector"] == [1, 2]          # no reflection after the pass
    prompts = [p["messages"][0]["content"] for ep, ev, s, _, p in trace(run) if ev == "llm.request"]
    assert "insight:" not in prompts[0] and prompts[2].count("insight:") == 2   # reflections reach later trials
    with psycopg.connect(DB) as conn:
        steps = conn.execute("select count(*) from run.step where run_id = %s", (run,)).fetchone()[0]
    assert rg.execute(f"rx_{out_dir.name}", initial_message(g, {"problem": "f"}), meta, timeout=120) == out
    with psycopg.connect(DB) as conn:
        assert conn.execute("select count(*) from run.step where run_id = %s", (run,)).fetchone()[0] == steps


def test_swapping_a_component_changes_what_runs(k, out_dir):
    system, by = graphs.reflexion(k)
    plays = next(a for a in k.query(predicate="k:plays", context=system, status=("accepted", "staged"))
                 if a.object.endswith("/slot/reflections"))
    k.supersede(plays.id, asserted_by=by, reason="try a memory that forgets",
                subject=k.node_id("urn:pc:component:memory/forgetful"))
    run, out, _ = compile_and_run(k, system, by, out_dir, f"rxf_{out_dir.name}", {"problem": "f"})
    assert out["ctl"]["exit"] == "retry.stop" and out["ctl"]["iter_loop_inject_reflections"] == 5
    assert out["ports"]["reflections.read"] == [] and "reflections" not in out["state"]
    evals = [p for _, ev, s, p, _ in trace(run) if ev == "slot.emit" and s == "episode_eval"]
    assert evals == ["fail"] * 5


def test_thermostat_runs_on_the_same_compiler(k, out_dir):
    system, by = graphs.thermostat(k)
    run, out, _ = compile_and_run(k, system, by, out_dir, f"th_{out_dir.name}", {"temp": 18.0})
    assert out["ctl"]["exit"] == "control.done" and out["ports"]["control.done"] == {"temp": 21.0}
    assert out["ctl"]["iter_loop_sensing"] == 7
    readings = [p for _, ev, s, port, p in trace(run) if ev == "slot.emit" and s == "sensing"]
    assert readings == [18.0, 18.5, 19.0, 19.5, 20.0, 20.5, 21.0]

"""M5 (without RuleGo): reading a system as a component graph, typed port contracts, the
compiler's plan and chain shapes, and the component adapter. tests/smoke/test_compile_run.py
runs the compiled chains in RuleGo."""
import copy
import subprocess
import sys
from pathlib import Path

import pytest

from kernel import Kernel
from modules.systems.graph import read_graph
from pclib import StepInput
from pclib.compile import CompileError, compile_graph, plan
from pclib.component import ComponentError, run
from tests import graphs

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def k(db_url, tmp_path):
    kern = Kernel.connect(db_url, data_root=tmp_path)
    kern.load_module("paper_compiler")
    kern.load_vocabulary(Path(__file__).parent / "control_loop.yaml")
    yield kern
    kern.conn.close()


def nodes(chain):
    return [n["id"] for n in chain["metadata"]["nodes"]]


def conns(chain):
    return {(x["fromId"], x["toId"], x["type"]) for x in chain["metadata"]["connections"]}


def test_reflexion_reads_as_a_typed_graph(k):
    system, _ = graphs.reflexion(k)
    g = read_graph(k, system)
    assert set(g["instances"]) == {"boundary", "actor", "test_gen", "episode_eval", "reflector", "reflections",
                                   "inject_reflections", "retry"}
    assert g["instances"]["reflections"]["entrypoint"] == "tests/components/reflexion/sliding_window.py"
    assert g["instances"]["reflections"]["params"] == {"max_items": 3}
    assert g["instances"]["actor"]["ports"]["start"] == {"dir": "in", "type": "context"}
    assert g["instances"]["test_gen"]["ports"]["tests"] == {"dir": "out", "type": "items"}
    shared = {(tuple(x["from"]), tuple(x["to"])) for x in g["couplings"] if x["kind"] == "shared_state"}
    assert shared == {(("test_gen", "tests"), ("episode_eval", "tests")),
                      (("reflections", "read"), ("inject_reflections", "input"))}
    assert g["port_types"]["feedback"]["required"] == ["passed"]


def test_incompatible_coupling_is_refused(k):
    def miswire(spec):
        spec["wiring"].append({"from": "reflections.read", "to": "actor.start", "provenance": "inferred"})
    system, _ = graphs.reflexion(k, miswire)
    errors = k.validate(system)
    assert any("carries 'items' into a port that takes 'context'" in e for e in errors)
    assert not [e for e in k.validate(graphs.reflexion(k)[0]) if "carries" in e]


def test_validate_spec_checks_types(tmp_path):
    import yaml
    spec = yaml.safe_load(graphs.SPEC.read_text())
    spec["wiring"].append({"from": "reflector.insight", "to": "actor.start", "provenance": "inferred"})
    bad = tmp_path / "spec.yaml"
    bad.write_text(yaml.safe_dump(spec))
    out = subprocess.run([sys.executable, "tools/validate_spec.py", str(bad)], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1 and "carries 'text' into a port that takes 'context'" in out.stdout


def test_reflexion_plan_and_chains(k):
    system, _ = graphs.reflexion(k)
    g = read_graph(k, system)
    p = plan(g)
    assert p["units"] == ["test_gen", "loop_inject_reflections"]
    assert p["loops"]["loop_inject_reflections"]["members"] == [
        "inject_reflections", "actor", "episode_eval", "reflector", "reflections", "retry"]
    root, body = compile_graph(g, "rx")
    assert root["ruleChain"]["root"]                              # both exits end the run: no exit switch
    assert nodes(root) == ["test_gen", "test_gen__route", "loop_inject_reflections"]
    assert ("test_gen__route", "loop_inject_reflections", "tests") in conns(root)
    loop = next(n for n in root["metadata"]["nodes"] if n["id"] == "loop_inject_reflections")
    assert loop["configuration"]["do"] == "chain:rx__loop_inject_reflections"
    assert body["ruleChain"]["id"] == "rx__loop_inject_reflections"
    assert nodes(body)[0] == "inject_reflections"
    assert ("episode_eval__route", "reflector", "fail") in conns(body)
    assert not any(t == "inject_reflections" for _, t, _ in conns(body))      # back edges end an iteration
    cfg = {n["id"]: n["_pclib"]["config"] for n in body["metadata"]["nodes"] if "_pclib" in n}
    assert cfg["inject_reflections"]["entry"] and cfg["inject_reflections"]["inputs"] == {
        "input": ["reflections.read"], "run": ["retry.retry", "test_gen.tests"]}
    assert cfg["episode_eval"]["exits"] == ["pass"] and cfg["retry"]["exits"] == ["stop"]
    assert cfg["reflections"]["routes"] == ["written"] and cfg["reflections"]["params"] == {"max_items": 3}


def test_thermostat_compiles(k):
    system, _ = graphs.thermostat(k)
    g = read_graph(k, system)
    assert g["instances"]["actuation"]["ports"]["current"] == {"dir": "in", "type": "celsius"}
    p = plan(g)
    assert p["units"] == ["loop_sensing"] and p["loops"]["loop_sensing"]["members"] == ["sensing", "control", "actuation"]
    root, body = compile_graph(g, "th")
    assert nodes(root) == ["loop_sensing"]
    assert not k.validate(system)


@pytest.mark.parametrize("couplings, error", [
    ([("boundary.start", "sensing.sample", "event"), ("sensing.reading", "control.input", "event"),
      ("sensing.reading", "actuation.current", "event"), ("control.done", "boundary.end", "event")], "fan-out"),
    ([("boundary.start", "sensing.sample", "event"), ("control.done", "boundary.end", "event")], "can't be reached"),
    ([("sensing.reading", "control.input", "event")], "nothing is coupled to boundary.start"),
])
def test_plan_refuses(k, couplings, error):
    g = read_graph(k, graphs.thermostat(k, couplings)[0])
    with pytest.raises(CompileError, match=error):
        plan(g)


def test_plan_refuses_two_loop_entries_and_nested_loops():
    def graph(edges):
        inst = {i: {"boundary": i == "b", "entrypoint": None if i == "b" else f"{i}.py", "ports": {}, "params": {}}
                for i in "babcd"}
        return {"instances": inst, "port_types": {},
                "couplings": [{"from": [a, pa], "to": [b, pb], "kind": "event"} for a, pa, b, pb in edges]}
    with pytest.raises(CompileError, match="exactly one instance entered from outside"):
        plan(graph([("b", "start", "a", "i"), ("a", "o", "c", "i"), ("a", "o2", "d", "i"),
                    ("c", "o", "d", "i2"), ("d", "o", "c", "i2")]))
    with pytest.raises(CompileError, match="nested loops"):
        plan(graph([("b", "start", "a", "i"), ("a", "o", "c", "i"), ("c", "o", "d", "i"),
                    ("d", "o", "c", "i2"), ("d", "o2", "a", "i2")]))


def test_missing_component_is_refused(k):
    system, by = graphs.thermostat(k)
    g = read_graph(k, system)
    g = copy.deepcopy(g)
    g["instances"]["control"]["entrypoint"] = None
    with pytest.raises(CompileError, match="control: no component"):
        plan(g)


def _inp(data):
    return StepInput(run_id="r", step_key="k", data=data)


def test_adapter_maps_ports_types_and_routing(monkeypatch):
    emitted = []
    monkeypatch.setattr("pclib.component.trace", lambda *a, **kw: emitted.append((a, kw)))
    config = {"inputs": {"run": ["a.x", "b.y"]}, "outputs": {"pass": "feedback", "fail": "feedback", "note": "any"},
              "routes": ["fail", "pass"], "exits": ["pass"], "loop": "L",
              "types": {"feedback": {"type": "object", "required": ["passed"]}, "any": {}}}
    bb = {"ports": {"a.x": 1, "b.y": 2}, "seq": {"a.x": 5, "b.y": 3}, "state": {}, "ctl": {}}
    seen = {}

    def main(inp):
        seen.update(inp.data)
        return {"data": {"outputs": {"pass": {"passed": True}, "note": "ok"}, "state": {"n": 1}}}
    out = run(main, "ev", config, _inp(bb))["data"]
    assert seen["inputs"] == {"run": 1}                          # the latest written source wins
    assert out["ports"]["ev.pass"] == {"passed": True} and out["state"]["ev"] == {"n": 1}
    assert out["ctl"] == {"port": "pass", "stop_L": True, "exit": "ev.pass"}
    assert [kw["port"] for _, kw in emitted] == ["pass", "note"]

    with pytest.raises(ComponentError, match="ev.fail \\(feedback\\)"):
        run(lambda i: {"data": {"outputs": {"fail": {"oops": 1}}}}, "ev", config, _inp(bb))
    with pytest.raises(ComponentError, match="only one flow port"):
        run(lambda i: {"data": {"outputs": {"fail": {"passed": False}, "pass": {"passed": True}}}}, "ev", config, _inp(bb))
    with pytest.raises(ComponentError, match="not one of its out ports"):
        run(lambda i: {"data": {"outputs": {"bogus": 1}}}, "ev", config, _inp(bb))

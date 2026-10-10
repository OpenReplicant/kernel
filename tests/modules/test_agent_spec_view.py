"""M3 gate: the agent-spec view round-trips spec.yaml, and validate catches what
tools/validate_spec.py catches."""
import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kernel import Kernel

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "reflexion" / "spec.yaml"


@pytest.fixture(scope="module")
def k(db_url, tmp_path_factory):
    kern = Kernel.connect(db_url, data_root=tmp_path_factory.mktemp("data"))
    kern.load_module("paper_compiler")
    yield kern
    kern.conn.close()


def spec(paper_id, mutate=None):
    s = yaml.safe_load(EXAMPLE.read_text())
    s["paper"]["id"] = paper_id
    if mutate:
        mutate(s)
    return s


def test_round_trip_is_exact_and_valid(k, tmp_path):
    original = spec("reflexion_rt")
    ctx = k.import_("agent-spec", copy.deepcopy(original))
    exported = k.export("agent-spec", ctx)
    assert exported == original
    out = tmp_path / "spec.yaml"
    out.write_text(yaml.safe_dump(exported, sort_keys=False))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_spec.py"), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_imported_system_validates_and_stated_facts_wait_for_evidence(k):
    ctx = k.import_("agent-spec", spec("reflexion_valid"))
    assert k.validate(ctx) == []
    staged = k.query(context=ctx, status=("staged",))
    assert staged and {a.method for a in staged} == {"stated"}
    repo = [a for a in staged if a.args.get("provenance") == "repo"]
    assert repo, "repo-labelled facts import as stated, label kept"


def _mcp_reflector(s):
    s["assets"].append({"id": "tools", "kind": "mcp_server", "provenance": "inferred",
                        "mcp": {"strategy": "registry", "tools": [{"name": "reflect", "description": "d",
                                                                  "backing": "chains/x.json"}]}})
    refl = next(x for x in s["slots"] if x["id"] == "reflector")
    refl["implementation"] = {"source": "registry", "kind": "mcp_tool", "ref": "reflect/verbal", "asset": "tools"}


def _undeclared_slot(s):
    s["wiring"].append({"from": "reflector.insight", "to": "ghost.run", "provenance": "inferred"})


def _two_policies(s):
    second = copy.deepcopy(next(x for x in s["slots"] if x["id"] == "actor"))
    second["id"] = "actor2"
    s["slots"].append(second)


@pytest.mark.parametrize("name, mutate, expect", [
    ("mcp", _mcp_reflector, "model-invoked mcp_tool"),
    ("ghost", _undeclared_slot, "belongs to no role instance"),
    ("twopol", _two_policies, "policy is exclusive"),
])
def test_validate_catches_what_the_validator_catches(k, tmp_path, name, mutate, expect):
    broken = spec(f"broken_{name}", mutate)
    errors = k.validate(k.import_("agent-spec", broken))
    assert any(expect in e for e in errors), errors
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(broken, sort_keys=False))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_spec.py"), str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout


def test_validating_without_the_domain_module_is_an_error(k, db_url, tmp_path):
    ctx = k.import_("agent-spec", spec("reflexion_partial"))
    partial = Kernel.connect(db_url, data_root=tmp_path)
    partial.load_module("systems")
    errors = partial.validate(ctx)
    assert any("vocabulary 'agent-design'" in e and "hasn't loaded" in e for e in errors), errors
    partial.conn.close()

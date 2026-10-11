"""M6 calibration plumbing: HumanEval completions are extracted and checked in the sandbox
(offline, on hand-written problems in the dataset's format), and the calibration driver runs
model calls through model_call in RuleGo against the fake endpoint."""
import gzip
import json
import os
import uuid
from pathlib import Path

import pytest

from bench import humaneval
from tests.fake_endpoint import catalog, serve
from tests.sandbox.test_sandbox import _engine_ready

pytestmark = pytest.mark.skipif(not _engine_ready(), reason="no container engine with the sandbox image")

ADD = {"task_id": "Toy/0", "entry_point": "add",
       "prompt": 'from typing import List\n\n\ndef add(xs: List[int]) -> int:\n    """Sum of xs."""\n',
       "canonical_solution": "    return sum(xs)\n",
       "test": "def check(candidate):\n    assert candidate([1, 2, 3]) == 6\n    assert candidate([]) == 0\n"}
LOOP = {**ADD, "task_id": "Toy/1", "test": "def check(candidate):\n    while True:\n        pass\n"}


def test_extract_code():
    assert humaneval.extract_code("Here:\n```python\ndef f():\n    return 1\n```\nDone") == "def f():\n    return 1\n"
    assert humaneval.extract_code("```\nx = 1\n```") == "x = 1\n"
    assert humaneval.extract_code("def f(): pass") == "def f(): pass"


def test_check_runs_the_problem_tests_in_the_sandbox(tmp_path):
    full = ADD["prompt"] + ADD["canonical_solution"]
    assert humaneval.check(ADD, full, tmp_path / "a")["passed"]
    assert not humaneval.check(ADD, full.replace("sum(xs)", "len(xs)"), tmp_path / "b")["passed"]
    r = humaneval.check(LOOP, full, tmp_path / "c", timeout_s=2)
    assert r["timed_out"] and not r["passed"]


def test_spread_and_task_id():
    assert humaneval.spread(list(range(164)), 20)[:3] == [0, 8, 16] and len(humaneval.spread(list(range(164)), 20)) == 20
    assert humaneval.task_id({"task_id": "HumanEval/12"}) == "HumanEval-12"


URL = os.environ.get("RULEGO_URL")
DB = os.environ.get("RULEGO_DATABASE_URL")


@pytest.mark.skipif(not (URL and DB), reason="RULEGO_URL and RULEGO_DATABASE_URL not set")
def test_calibration_through_model_call(tmp_path, monkeypatch):
    from bench.calibrate import calibrate
    from kernel import Kernel
    from pclib.rulego import RuleGo

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("PC_DATA", str(tmp_path / "data"))
    data = tmp_path / "toy.jsonl.gz"
    probs = [{**ADD, "task_id": f"Toy/{i}"} for i in range(4)]
    with gzip.open(data, "wt") as f:
        f.write("".join(json.dumps(p) + "\n" for p in probs))

    def reply(body, n):                         # right on odd calls, wrong on even ones
        code = ADD["prompt"] + ("    return sum(xs)\n" if n % 2 else "    return 0\n")
        return f"Sure.\n```python\n{code}```"
    tag = uuid.uuid4().hex[:6]
    with serve(reply=reply) as (base, calls):
        (tmp_path / "models.yaml").write_text(catalog(tag, base))
        k = Kernel.connect(DB, data_root=tmp_path / "data")
        # small-a costs $0.002 a call: three calls fit in $0.005, the fourth is refused
        s = calibrate(k, RuleGo(URL), f"small-a-{tag}", n=4, budget_usd=0.005,
                      catalog=tmp_path / "models.yaml", dataset=data)
        k.conn.close()
    assert len(calls) == 3 and "def add" in calls[0]["body"]["messages"][0]["content"]
    assert s["attempted"] == 3 and s["passed"] == 2 and s["pass_rate"] == pytest.approx(0.667)
    assert [r["passed"] for r in s["results"]] == [True, False, True]
    assert s["served_models"] == {f"small-a-{tag}": 3} and s["tokens"]["output"] == 1500
    assert s["results"][0]["usage"]["input_tokens"] == 1000
    saved = json.loads((tmp_path / "data/runs" / s["run_id"] / "calibration.json").read_text())
    assert saved["passed"] == 2

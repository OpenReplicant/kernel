"""M0 gate against a running RuleGo-Server (rulego/Containerfile) with this repo at /repo.
Skipped unless RULEGO_URL is set, e.g. RULEGO_URL=http://127.0.0.1:9090."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("RULEGO_URL")
pytestmark = pytest.mark.skipif(not URL, reason="RULEGO_URL not set")


@pytest.fixture(scope="module", autouse=True)
def deployed():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "rulego_deploy.py"),
                    str(ROOT / "rulego" / "spike" / "chains")], check=True, capture_output=True)


def execute(chain, body):
    req = urllib.request.Request(f"{URL}/api/v1/rules/{chain}/execute/STEP", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_gate_sequence_loop_branch_subchain_sync_result():
    status, body = execute("m0_main", {"run_id": "r", "task_id": "t", "episode": 0, "data": {}})
    assert status == 200, body
    out = json.loads(body)
    assert out["run_id"] == "r" and out["data"]["count"] == 3 and out["data"]["doubled"] == 6
    assert out["data"]["trail"] == ["inc", "judge", "odd", "double", "inc", "judge", "even", "double",
                                    "inc", "judge", "odd", "double", "final"]


def test_self_recursion_through_flow_node():
    status, body = execute("m0_recurse", {"data": {"n": 25}})
    assert status == 200 and json.loads(body)["data"]["count"] == 25


def test_exit_code_reaches_the_caller():
    assert execute("m0_probe", {"data": {"exit": 75}}) == (400, "exit status 75")


def test_argument_over_128k_is_refused():
    status, body = execute("m0_probe", {"data": {"big": "x" * 140_000}})
    assert status == 400 and "argument list too long" in body

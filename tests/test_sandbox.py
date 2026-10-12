"""The S4 gate (docs/PLAN.md): untrusted code runs only in the sandbox, behind an MCP server.

Code run through the sandbox_run node can't reach the network, write outside /tmp, see the
host's environment, exceed its memory or process limits, or outlive its timeout; each run is
in the graph; switched off in the config, the same call runs on the `local` backend and is
recorded as not isolated. And Haiku's code for a small task is tested in the sandbox, the
observed result tracing back through the run to the model call that wrote the code.
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import needs_claude
from kernel import claims, config, modules
from kernel.nodes import sandbox_run

ROOT = Path(__file__).resolve().parent.parent
CODER = ROOT / "tests" / "modules" / "coder"

PROBE = r'''
import json, os, socket
out = {"uid": os.getuid(), "env": sorted(os.environ)}
try:
    socket.create_connection(("1.1.1.1", 53), 2); out["network"] = True
except OSError:
    out["network"] = False
for path in ("/etc/owned", "/work/owned", "/tmp/ok"):
    try:
        open(path, "w").write("x"); out[path] = "written"
    except OSError as e:
        out[path] = e.strerror
print(json.dumps(out))
'''


def engine_ready():
    image = config.load()["sandbox"]["images"]["python"]
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not engine_ready(), reason="needs docker with the sandbox image pulled")


def python(code: str, **kw) -> dict:
    return {"command": ["python", "main.py"], "files": {"main.py": code}, **kw}


def test_the_interface_is_one_mcp_tool():
    async def tools():
        from mcp import Client
        async with Client(sandbox_run.server_params(config.load())) as c:
            return (await c.list_tools()).tools
    [tool] = asyncio.run(tools())
    assert tool.name == "sandbox_run"
    assert set(tool.input_schema["properties"]) == {"command", "files", "image", "timeout_s", "cpus", "memory",
                                                    "pids", "max_output"}  # no network, mounts, user or flags


def test_the_sandbox_holds(conn, monkeypatch):
    monkeypatch.setenv("SANDBOX_TEST_SECRET", "must-not-leak")
    r = sandbox_run.run(conn, python(PROBE))
    seen = json.loads(r["stdout"])
    assert r["exit_code"] == 0 and r["backend"] == "docker" and r["isolated"] is True
    assert seen["network"] is False
    assert seen["/etc/owned"] == "Read-only file system" and seen["/work/owned"] == "Read-only file system"
    assert seen["/tmp/ok"] == "written"
    assert seen["uid"] == 10001
    host_only = {"SANDBOX_TEST_SECRET", "DATABASE_URL", "KERNEL_GRAPH", "HTTPS_PROXY", "GH_TOKEN"}
    assert not host_only & set(seen["env"])  # only what the image itself sets


def test_memory_and_process_limits(conn):
    r = sandbox_run.run(conn, python("x = bytearray(300 * 1024 * 1024)\nprint('allocated')", memory="64m"))
    assert r["exit_code"] != 0 and "allocated" not in r["stdout"] and r["limits"]["memory"] == "64m"
    fork = ("import subprocess\nps = [subprocess.Popen(['sleep', '5']) for _ in range(40)]\nprint('forked', len(ps))")
    r = sandbox_run.run(conn, python(fork, pids=16))
    assert r["exit_code"] != 0 and "forked" not in r["stdout"] and "Resource temporarily unavailable" in r["stderr"]


def test_the_timeout_removes_the_container(conn):
    r = sandbox_run.run(conn, python("while True:\n    pass", timeout_s=2))
    assert r["timed_out"] is True and r["exit_code"] is None and 2 <= r["duration_s"] < 30
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={r['container']}"], capture_output=True,
                          text=True).stdout
    assert left.strip() == ""


def test_limits_are_capped_and_images_allowlisted(conn):
    r = sandbox_run.run(conn, python("print(1)", memory="100g", cpus=64, timeout_s=10_000))
    assert r["limits"]["memory"] == "1g" and r["limits"]["cpus"] == 2.0 and r["limits"]["timeout_s"] == 300
    with pytest.raises(RuntimeError, match="not allowed"):
        sandbox_run.run(conn, python("print(1)", image="docker.io/library/alpine"))
    refused = latest_run(conn)
    assert refused["status"] == "refused" and "not allowed" in refused["error"]


def test_runs_are_recorded(conn):
    r = sandbox_run.run(conn, {**python("print('hello')"), "inputs": {"why": "a test"}})
    src = conn.execute("MATCH (s:source) WHERE id(s) = %s::graphid RETURN properties(s)", (r["source"],)).fetchone()[0]
    conn.commit()
    assert src["kind"] == "sandbox_run" and src["uri"] == "sandbox:docker"
    c = src["content"]
    assert c["status"] == "ok" and c["inputs"] == {"why": "a test"}
    assert c["request"]["files"] == {"main.py": "print('hello')"} and c["result"]["stdout"] == "hello\n"
    assert c["result"]["isolated"] is True and c["config"].endswith("kernel.yaml")


def test_switched_off_runs_locally_and_says_so(conn, tmp_path):
    cfg = yaml.safe_load((ROOT / "config" / "kernel.yaml").read_text())
    cfg["sandbox"]["enabled"] = False
    (tmp_path / "off.yaml").write_text(yaml.safe_dump(cfg))
    off = config.load(tmp_path / "off.yaml")
    r = sandbox_run.run(conn, {"command": ["python3", "-c", "import os; print(sorted(os.environ))"]}, cfg=off)
    assert r["backend"] == "local" and r["isolated"] is False
    # still nothing from the environment (Python sets LC_CTYPE itself under the C locale, PEP 538)
    assert set(json.loads(r["stdout"].replace("'", '"'))) <= {"HOME", "PATH", "LC_CTYPE"}
    r = sandbox_run.run(conn, {"command": ["python3", "-c", "while True: pass"], "timeout_s": 1}, cfg=off)
    assert r["timed_out"] is True
    assert latest_run(conn)["result"]["isolated"] is False and latest_run(conn)["config"] == str(tmp_path / "off.yaml")


def latest_run(conn) -> dict:
    row = conn.execute("""MATCH (s:source) WHERE s.kind = 'sandbox_run'
                          RETURN properties(s) ORDER BY s.recorded_at DESC LIMIT 1""").fetchone()
    conn.commit()
    return row[0]["content"]


@needs_claude
def test_haiku_writes_code_and_the_sandbox_tests_it(conn, rulego):
    """In the entity's terms: Haiku writes a function, the sandbox runs the task's tests on it,
    and the observed result traces back through the run to the model call that wrote it."""
    from kernel import db
    with db.connect() as c:
        modules.install(CODER, c, rulego)
    rulego.execute("models_catalog", {})  # the models module's own catalog (Haiku through claude -p)
    task = {"task": "median",
            "spec": "Write solution.py defining median(xs: list[float]) -> float, the median of a non-empty list "
                    "(the mean of the two middle values when the length is even).",
            "tests": "from solution import median\nassert median([3, 1, 2]) == 2\n"
                     "assert median([4, 1, 3, 2]) == 2.5\nassert median([7]) == 7\nprint('passed')\n"}
    out = rulego.execute("coder_solve", task)
    assert out["sandbox_result"]["stdout"].strip() == "passed", out["sandbox_result"]

    [passed] = claims.held(conn, out["written"]["things"]["task"], "coder.passed")
    conn.commit()
    w = claims.why(conn, passed["id"])
    assert passed["value"] is True and passed["method"] == "observed" and w["agent"]["name"] == "module:coder"
    run = w["source"]
    assert run["kind"] == "sandbox_run" and run["content"]["result"]["isolated"] is True
    call_id = run["content"]["inputs"]["model_call"]
    call = conn.execute("MATCH (s:source) WHERE id(s) = %s::graphid RETURN properties(s)", (call_id,)).fetchone()[0]
    conn.commit()
    assert call["kind"] == "model_call" and call["content"]["served_by"].startswith("claude-haiku")
    assert "def median" in run["content"]["request"]["files"]["solution.py"]
    assert run["content"]["request"]["files"]["solution.py"] in call["content"]["reply"]["text"]

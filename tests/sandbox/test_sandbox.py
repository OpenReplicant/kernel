"""M6 gate: payloads run in a fresh, locked-down container. A passing solution passes, a
failing one fails, an infinite loop times out, and network access fails. Also: the container
sees only its work directory, gets no host environment, can't write outside /tmp, runs as a
non-root user, and a step's sandbox calls are traced.

Needs the engine ($PC_CONTAINER_CMD, default podman) and the image:
    podman build -t localhost/kernel-sandbox sandbox
"""
import io
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from pclib import run_step, sandbox

ROOT = Path(__file__).resolve().parents[2]

SOLUTION = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return a - b\n"
TESTS = "from solution import add\n\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint('ok')\n"
CHECK = ["python", "test_solution.py"]


def _engine_ready():
    try:
        r = subprocess.run([*shlex.split(os.environ.get("PC_CONTAINER_CMD", "podman")), "image", "inspect",
                            sandbox.IMAGE], capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _engine_ready(), reason=f"no container engine with {sandbox.IMAGE}")


def run(tmp_path, files, command=CHECK, **limits):
    return sandbox.run(tmp_path / "work", command, files, **limits)


def test_passing_solution_passes(tmp_path):
    r = run(tmp_path, {"solution.py": SOLUTION, "test_solution.py": TESTS})
    assert r["exit_code"] == 0 and not r["timed_out"], r
    assert r["stdout"].strip() == "ok"


def test_failing_solution_fails(tmp_path):
    r = run(tmp_path, {"solution.py": WRONG, "test_solution.py": TESTS})
    assert r["exit_code"] == 1 and not r["timed_out"]
    assert "AssertionError" in r["stderr"]


def test_infinite_loop_times_out_and_the_container_is_gone(tmp_path):
    r = run(tmp_path, {"main.py": "while True:\n    pass\n"}, ["python", "main.py"], timeout_s=2)
    assert r["timed_out"] and r["exit_code"] is None
    assert r["duration_s"] < 30
    engine = shlex.split(os.environ.get("PC_CONTAINER_CMD", "podman"))
    left = subprocess.run([*engine, "ps", "-aq", "--filter", "name=pc-sandbox-"], capture_output=True, text=True)
    assert left.stdout.strip() == ""


NET = """import socket, sys
for host, port in [("1.1.1.1", 53), ("pypi.org", 443)]:
    try:
        socket.create_connection((host, port), timeout=3).close()
    except OSError as e:
        print(host, type(e).__name__, file=sys.stderr)
    else:
        print("reached", host)
        sys.exit(0)
sys.exit(3)
"""


def test_network_access_fails(tmp_path):
    r = run(tmp_path, {"main.py": NET}, ["python", "main.py"])
    assert r["exit_code"] == 3, r
    assert "reached" not in r["stdout"]


PROBE = """import json, os, pathlib
def writable(p):
    try:
        pathlib.Path(p).write_text("x")
        return True
    except OSError:
        return False
print(json.dumps({"uid": os.getuid(), "env": sorted(os.environ), "work": sorted(os.listdir("/work")),
                  "repo": os.path.exists("/repo"), "root_w": writable("/escape"),
                  "work_w": writable("/work/escape"), "tmp_w": writable("/tmp/scratch")}))
"""


def test_container_sees_only_its_work_dir_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-leak")
    (tmp_path / "secret.txt").write_text("outside the work dir")
    r = run(tmp_path, {"main.py": PROBE}, ["python", "main.py"])
    assert r["exit_code"] == 0, r
    p = json.loads(r["stdout"])
    assert p["uid"] == 10001
    assert p["work"] == ["main.py"] and not p["repo"]
    assert not any(k in p["env"] for k in ("ANTHROPIC_API_KEY", "DATABASE_URL"))
    assert not p["root_w"] and not p["work_w"] and p["tmp_w"]
    assert not (tmp_path / "work" / "escape").exists()


def test_limits_hold(tmp_path):
    fork = "import os\nfor _ in range(500):\n    os.fork() == 0 and __import__('time').sleep(30)\n"
    r = run(tmp_path, {"main.py": fork}, ["python", "main.py"], pids=32, timeout_s=10)
    assert r["exit_code"] not in (0, None) and "BlockingIOError" in r["stderr"]
    hog = "x = bytearray(512 * 1024 * 1024)\n"
    r = run(tmp_path, {"hog.py": hog}, ["python", "hog.py"], memory="128m")
    assert r["exit_code"] not in (0, None)


def test_output_is_capped(tmp_path):
    r = run(tmp_path, {"main.py": "print('y' * 100000)\n"}, ["python", "main.py"], max_output=1000)
    assert r["exit_code"] == 0
    assert r["stdout"].startswith("y" * 1000) and "more bytes]" in r["stdout"] and len(r["stdout"]) < 1100


def test_files_stay_inside_the_work_dir(tmp_path):
    with pytest.raises(ValueError, match="outside the work directory"):
        run(tmp_path, {"../escape.py": "print(1)"})


def test_tool_script_round_trip(tmp_path):
    inp = {"work_dir": str(tmp_path / "work"), "files": {"solution.py": SOLUTION, "test_solution.py": TESTS},
           "command": CHECK}
    out = subprocess.run([sys.executable, "scripts/env_container.py"], cwd=ROOT, input=json.dumps(inp),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["exit_code"] == 0


def test_a_steps_sandbox_calls_are_traced(db_url, conn, run_id, tmp_path, monkeypatch):
    monkeypatch.setenv("PC_DATA", str(tmp_path / "data"))

    def main(inp):
        r = sandbox.run(sandbox.work_dir(inp), CHECK, {"solution.py": WRONG, "test_solution.py": TESTS})
        return {"data": {"passed": r["exit_code"] == 0}}
    env = {"run_id": run_id, "step_key": f"task=T/ep=1/check-{uuid.uuid4().hex[:4]}", "task_id": "T",
           "episode": 1, "data": {}}
    out = io.StringIO()
    assert run_step(main, argv=[json.dumps(env)], stdout=out) == 0
    assert json.loads(out.getvalue())["data"] == {"passed": False}
    assert (tmp_path / "data/runs" / run_id / "tasks/T/ep-1/work/solution.py").read_text() == WRONG
    rows = conn.execute("select event, payload from run.trace where run_id = %s and step_key = %s order by id",
                        (run_id, env["step_key"])).fetchall()
    assert [e for e, _ in rows] == ["tool.call", "tool.result"]
    assert rows[0][1]["command"] == CHECK and rows[1][1]["exit_code"] == 1

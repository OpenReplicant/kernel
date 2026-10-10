"""M1 gate: the script contract (docs/SCRIPT_CONTRACT.md)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pclib import EXIT_FAILED, EXIT_RETRYABLE, TRACE_EVENTS

ROOT = Path(__file__).resolve().parents[2]
STEP = Path(__file__).parent / "steps" / "counting_step.py"


def invoke(db_url, payload, *args, stdin=True):
    """Run the step with the envelope on stdin, or as the last argument (how RuleGo calls it)."""
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(ROOT)}
    cmd = [sys.executable, str(STEP), *args] + ([] if stdin else [json.dumps(payload)])
    return subprocess.run(cmd, input=json.dumps(payload) if stdin else None,
                          capture_output=True, text=True, env=env, timeout=60)


def envelope(run_id, tmp_path, step_key="task=T1/trial=1/counter", **data):
    return {"run_id": run_id, "step_key": step_key, "task_id": "T1", "episode": 1,
            "data": {"counter": str(tmp_path / "count.txt"), **data}, "meta": {"m": 1}}


def test_second_invocation_returns_stored_output_without_rerunning(db_url, run_id, tmp_path):
    first = invoke(db_url, envelope(run_id, tmp_path, n=41))
    second = invoke(db_url, envelope(run_id, tmp_path, n=41))
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout) == {"run_id": run_id, "task_id": "T1", "episode": 1,
                                        "data": {"counter": str(tmp_path / "count.txt"), "executions": 1,
                                                 "n": 42, "padding": ""},
                                        "meta": {"echo": {"m": 1}}}
    assert second.stdout == first.stdout
    assert (tmp_path / "count.txt").read_text() == "ran\n"      # executed once


def test_stdout_carries_only_json(db_url, run_id, tmp_path):
    r = invoke(db_url, envelope(run_id, tmp_path, step_key="noisy", noise=True))
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("\n") == 1
    json.loads(r.stdout)
    assert "stray print" in r.stderr


def test_trace_rows_readable_by_run_task_episode(db_url, conn, run_id, tmp_path):
    r = invoke(db_url, envelope(run_id, tmp_path, step_key="traced", n=7))
    assert r.returncode == 0, r.stderr
    rows = conn.execute(
        "select event, slot, port, payload, step_key from run.trace"
        " where run_id = %s and task_id = 'T1' and episode = 1 order by id", (run_id,)).fetchall()
    assert rows == [("step.start", "counter", None, None, "traced"),
                    ("slot.emit", "counter", "done", {"n": 7}, "traced")]


@pytest.mark.parametrize("flag, code", [("fail", EXIT_FAILED), ("retry", EXIT_RETRYABLE)])
def test_failure_leaves_no_rows(db_url, conn, run_id, tmp_path, flag, code):
    r = invoke(db_url, envelope(run_id, tmp_path, step_key=f"bad-{flag}", **{flag: True}))
    assert r.returncode == code
    assert r.stdout == ""
    assert conn.execute("select count(*) from run.step where run_id = %s", (run_id,)).fetchone()[0] == 0
    assert conn.execute("select count(*) from run.trace where run_id = %s", (run_id,)).fetchone()[0] == 0


def test_rerun_after_failure_executes_and_traces_once(db_url, conn, run_id, tmp_path):
    assert invoke(db_url, envelope(run_id, tmp_path, step_key="flaky", fail=True)).returncode == EXIT_FAILED
    r = invoke(db_url, envelope(run_id, tmp_path, step_key="flaky"))
    assert r.returncode == 0, r.stderr
    n = conn.execute("select count(*) from run.trace where run_id = %s and event = 'slot.emit'",
                     (run_id,)).fetchone()[0]
    assert n == 1


def test_envelope_as_argument_chains_into_the_next_step(db_url, conn, run_id, tmp_path):
    """RuleGo passes ${data} as the last argument; the printed envelope is the next input."""
    env = envelope(run_id, tmp_path, n=1)
    del env["step_key"]
    first = invoke(db_url, env, "--step", "first", stdin=False)
    assert first.returncode == 0, first.stderr
    nxt = json.loads(first.stdout)
    second = invoke(db_url, nxt, "--step", "second", stdin=False)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["data"]["n"] == 3
    keys = [r[0] for r in conn.execute("select step_key from run.step where run_id = %s order by step_key",
                                       (run_id,))]
    assert keys == ["task=T1/ep=1/first", "task=T1/ep=1/second"]


def test_timeout_fails_the_step(db_url, conn, run_id, tmp_path):
    r = invoke(db_url, envelope(run_id, tmp_path, step_key="slow", sleep=5), "--timeout", "0.5")
    assert r.returncode == EXIT_FAILED and "StepTimeout" in r.stderr
    assert conn.execute("select count(*) from run.step where run_id = %s", (run_id,)).fetchone()[0] == 0


def test_oversized_output_is_refused_before_storing(db_url, conn, run_id, tmp_path):
    r = invoke(db_url, envelope(run_id, tmp_path, step_key="big", pad=130_000))
    assert r.returncode == EXIT_FAILED and "pass a reference" in r.stderr and r.stdout == ""
    assert conn.execute("select count(*) from run.step where run_id = %s", (run_id,)).fetchone()[0] == 0


def test_bad_input_is_a_permanent_failure(db_url):
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, str(STEP)], input='{"step_key": "x"}',
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == EXIT_FAILED and r.stdout == "" and "run_id" in r.stderr


def test_trace_event_names_match_spec_schema():
    schema = json.loads((ROOT / "schema" / "spec.schema.json").read_text())
    assert TRACE_EVENTS == set(schema["$defs"]["traceEvent"]["properties"]["event"]["enum"])

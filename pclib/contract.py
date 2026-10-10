"""The script contract (docs/SCRIPT_CONTRACT.md).

A step script is a function from the input envelope to {"data": ..., "meta": ...}:

    from pclib import run_step, trace

    def main(inp):
        trace("slot.emit", slot="reflector", port="insight", payload={...})
        return {"data": {...}}

    if __name__ == "__main__":
        run_step(main)

run_step reads one JSON object from stdin, returns the stored output if this
(run_id, step_key) already finished, otherwise runs the function and commits its
output, usage and trace events in one transaction, then prints the output as the
only thing on stdout. A failed step leaves no rows, so a rerun never duplicates events.
"""
import contextlib
import json
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from psycopg.types.json import Jsonb

from .config import connect

EXIT_OK = 0
EXIT_RETRYABLE = 75
EXIT_FAILED = 1

# Must match traceEvent in schema/spec.schema.json (a contract test checks this).
TRACE_EVENTS = frozenset({
    "episode.start", "episode.end", "step.start", "step.end",
    "llm.request", "llm.response", "tool.call", "tool.result",
    "slot.emit", "memory.write", "memory.read", "env.snapshot", "env.restore",
    "controller.decision",
})


class Retryable(Exception):
    """Raise for transient failures (rate limit, network); the script exits 75."""


@dataclass
class StepInput:
    run_id: str
    step_key: str
    task_id: str | None = None
    episode: int | None = None
    data: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str) -> "StepInput":
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("input must be a JSON object")
        missing = [k for k in ("run_id", "step_key") if not obj.get(k)]
        if missing:
            raise ValueError(f"input is missing {', '.join(missing)}")
        unknown = set(obj) - {"run_id", "step_key", "task_id", "episode", "data", "meta"}
        if unknown:
            raise ValueError(f"unknown input keys: {', '.join(sorted(unknown))}")
        return cls(**obj)


@dataclass
class _Step:
    inp: StepInput
    events: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)


_current: _Step | None = None


def current() -> StepInput:
    if _current is None:
        raise RuntimeError("not inside run_step")
    return _current.inp


def trace(event: str, slot: str | None = None, port: str | None = None, payload: Any = None):
    """Record a semantic event for this step; written when the step commits."""
    if _current is None:
        raise RuntimeError("trace() called outside run_step")
    if event not in TRACE_EVENTS:
        raise ValueError(f"unknown trace event {event!r}")
    _current.events.append((event, slot, port, payload))


def add_usage(**counts: float):
    """Accumulate model usage (tokens, cost) for this step's run.step row."""
    if _current is None:
        raise RuntimeError("add_usage() called outside run_step")
    for k, v in counts.items():
        _current.usage[k] = _current.usage.get(k, 0) + v


def _stored_output(conn, inp: StepInput):
    row = conn.execute("select output from run.step where run_id = %s and step_key = %s",
                       (inp.run_id, inp.step_key)).fetchone()
    return row[0] if row else None


def _commit(conn, step: _Step, output: dict):
    inp = step.inp
    with conn.transaction():
        inserted = conn.execute(
            "insert into run.step (run_id, step_key, output, usage) values (%s, %s, %s, %s)"
            " on conflict do nothing returning 1",
            (inp.run_id, inp.step_key, Jsonb(output), Jsonb(step.usage) if step.usage else None),
        ).fetchone()
        if not inserted:                      # a concurrent run of this step finished first
            return _stored_output(conn, inp)
        with conn.cursor() as cur:
            cur.executemany(
                "insert into run.trace (run_id, task_id, episode, step_key, event, slot, port, payload)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(inp.run_id, inp.task_id, inp.episode, inp.step_key, ev, slot, port,
                  None if payload is None else Jsonb(payload))
                 for ev, slot, port, payload in step.events])
    return output


def run_step(fn: Callable[[StepInput], dict], stdin=None, stdout=None) -> int:
    """Run one step under the contract. Exits the process unless stdin/stdout are given."""
    global _current
    stdin = stdin or sys.stdin
    real_stdout = stdout or sys.stdout
    code = EXIT_FAILED
    try:
        inp = StepInput.parse(stdin.read())
        with connect(autocommit=True) as conn:
            output = _stored_output(conn, inp)
            if output is None:
                _current = _Step(inp)
                # Anything the step prints goes to stderr; stdout carries only the result.
                with contextlib.redirect_stdout(sys.stderr):
                    result = fn(inp)
                if not isinstance(result, dict) or "data" not in result:
                    raise TypeError("step must return a dict with a 'data' key")
                if set(result) - {"data", "meta"}:
                    raise TypeError("step output may only have 'data' and 'meta' keys")
                output = _commit(conn, _current, result)
        real_stdout.write(json.dumps(output, sort_keys=True))   # same bytes on replay
        real_stdout.write("\n")
        real_stdout.flush()
        code = EXIT_OK
    except Retryable:
        traceback.print_exc(file=sys.stderr)
        code = EXIT_RETRYABLE
    except Exception:
        traceback.print_exc(file=sys.stderr)
        code = EXIT_FAILED
    finally:
        _current = None
    if stdout is None:
        sys.exit(code)
    return code

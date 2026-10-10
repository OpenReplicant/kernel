"""The script contract (docs/SCRIPT_CONTRACT.md).

A step script is a function from the input envelope to {"data": ..., "meta": ...}:

    from pclib import run_step, trace

    def main(inp):
        trace("slot.emit", slot="reflector", port="insight", payload={...})
        return {"data": {...}}

    if __name__ == "__main__":
        run_step(main)

Invocation: `step.py [--step NAME] [--timeout SECONDS] [ENVELOPE]`. RuleGo's exec node has
no stdin, so chains pass the envelope as the last argument (`${data}`); without it, the
envelope is read from stdin.

run_step returns the stored output if this (run_id, step_key) already finished, otherwise
runs the function and commits its output, usage and trace events in one transaction. It
prints the envelope for the next step (the input's run, task and episode with the new data
and meta) as the only thing on stdout. A failed step leaves no rows, so a rerun never
duplicates events.
"""
import argparse
import contextlib
import json
import os
import signal
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from psycopg.types.json import Jsonb

from .config import connect

EXIT_OK = 0
EXIT_RETRYABLE = 75
EXIT_FAILED = 1

# The envelope travels as one argv element between chain steps; Linux caps a single
# argument at 128 KiB (MAX_ARG_STRLEN). Larger content goes by reference (docs/RULEGO_NOTES.md).
MAX_ENVELOPE_BYTES = int(os.environ.get("PC_MAX_ENVELOPE_BYTES", 120_000))

# Must match traceEvent in schema/spec.schema.json (a contract test checks this).
TRACE_EVENTS = frozenset({
    "episode.start", "episode.end", "step.start", "step.end",
    "llm.request", "llm.response", "tool.call", "tool.result",
    "slot.emit", "memory.write", "memory.read", "env.snapshot", "env.restore",
    "controller.decision",
})

ENVELOPE_KEYS = {"run_id", "step_key", "task_id", "episode", "data", "meta"}


class Retryable(Exception):
    """Raise for transient failures (rate limit, network); the script exits 75."""


class StepTimeout(Exception):
    pass


@dataclass
class StepInput:
    run_id: str
    step_key: str
    task_id: str | None = None
    episode: int | None = None
    data: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str, step_name: str | None = None) -> "StepInput":
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("input must be a JSON object")
        unknown = set(obj) - ENVELOPE_KEYS
        if unknown:
            raise ValueError(f"unknown input keys: {', '.join(sorted(unknown))}")
        if not obj.get("run_id"):
            raise ValueError("input is missing run_id")
        if not obj.get("step_key"):
            if not step_name:
                raise ValueError("input is missing step_key (or pass --step)")
            obj["step_key"] = f"task={obj.get('task_id')}/ep={obj.get('episode')}/{step_name}"
        return cls(**obj)

    def next_envelope(self, output: dict) -> dict:
        """What the next step receives: same run and task, this step's data and meta."""
        env = {"run_id": self.run_id, "task_id": self.task_id,
               "episode": output.get("episode", self.episode),
               "data": output["data"], "meta": output.get("meta", {})}
        return {k: v for k, v in env.items() if v is not None}


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


def _render(inp: StepInput, output: dict) -> str:
    # Sorted keys: a replayed step prints the same bytes.
    text = json.dumps(inp.next_envelope(output), sort_keys=True)
    if len(text.encode()) > MAX_ENVELOPE_BYTES:
        raise ValueError(f"output envelope is {len(text.encode())} bytes, over {MAX_ENVELOPE_BYTES}; "
                         "store large content (prompts, code, logs) and pass a reference")
    return text


def _check_result(result) -> dict:
    if not isinstance(result, dict) or "data" not in result:
        raise TypeError("step must return a dict with a 'data' key")
    if set(result) - {"data", "meta", "episode"}:
        raise TypeError("step output may only have 'data', 'meta' and 'episode' keys")
    return result


def _on_alarm(signum, frame):
    raise StepTimeout("step exceeded its --timeout")


def run_step(fn: Callable[[StepInput], dict], argv=None, stdin=None, stdout=None) -> int:
    """Run one step under the contract. Exits the process unless stdout is given."""
    global _current
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", help="step name, used to build step_key when the envelope has none")
    ap.add_argument("--timeout", type=float, help="wall-clock limit for this step, seconds")
    ap.add_argument("envelope", nargs="?", help="input envelope as JSON (default: stdin)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    real_stdout = stdout or sys.stdout
    code = EXIT_FAILED
    try:
        raw = args.envelope if args.envelope is not None else (stdin or sys.stdin).read()
        inp = StepInput.parse(raw, args.step or Path(sys.argv[0]).stem)
        with connect(autocommit=True) as conn:
            output = _stored_output(conn, inp)
            if output is None:
                _current = _Step(inp)
                if args.timeout:
                    signal.signal(signal.SIGALRM, _on_alarm)
                    signal.setitimer(signal.ITIMER_REAL, args.timeout)
                try:
                    # Anything the step prints goes to stderr; stdout carries only the result.
                    with contextlib.redirect_stdout(sys.stderr):
                        result = _check_result(fn(inp))
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                _render(inp, result)          # size check before anything is stored
                output = _commit(conn, _current, result)
        real_stdout.write(_render(inp, output) + "\n")
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

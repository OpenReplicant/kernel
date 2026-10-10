#!/usr/bin/env python3
"""Fixture step for contract tests. Appends a line to data.counter on every real
execution, so a test can tell whether the step ran again or replayed its stored output."""
from pathlib import Path

from pclib import Retryable, run_step, trace


def main(inp):
    d = inp.data
    with open(d["counter"], "a") as f:
        f.write("ran\n")
    if d.get("noise"):
        print("stray print that must not reach stdout")
    trace("step.start", slot="counter")
    trace("slot.emit", slot="counter", port="done", payload={"n": d.get("n", 0)})
    if d.get("retry"):
        raise Retryable("transient")
    if d.get("fail"):
        raise RuntimeError("permanent")
    lines = Path(d["counter"]).read_text().count("\n")
    return {"data": {"executions": lines, "n": d.get("n", 0) + 1}, "meta": {"echo": inp.meta}}


if __name__ == "__main__":
    run_step(main)

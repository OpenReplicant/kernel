#!/usr/bin/env python3
"""Run a command over a work directory in a fresh, locked-down container (pclib/sandbox.py).
Schemas: schemas/env_container.*"""
from _tool import run_tool

from pclib import sandbox


def main(inp):
    limits = {k: inp.get(k) for k in sandbox.LIMITS}
    return sandbox.run(inp["work_dir"], inp.get("command", ["python", "main.py"]), inp.get("files"),
                       image=inp.get("image", sandbox.IMAGE), **limits)


if __name__ == "__main__":
    run_tool("env_container", main, kernel=False)

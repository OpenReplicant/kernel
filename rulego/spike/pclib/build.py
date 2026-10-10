#!/usr/bin/env python3
"""Generate the pclib spike chains (the M0 gate on pclib x/python nodes) into ./chains."""
from pathlib import Path

from pclib import chains as c

HERE = Path(__file__).resolve().parent
S = "rulego/spike/pclib/steps"

CHAINS = [
    c.chain("pc_iter",
            nodes=[c.step("inc", f"{S}/inc.py"), c.step("judge", f"{S}/judge.py"),
                   c.switch("branch", [("msg.passed == true", "Even")]),
                   c.step("even", f"{S}/even.py"), c.step("odd", f"{S}/odd.py"),
                   c.subchain("sub", "pc_sub")],
            edges=[("inc", "judge"), ("judge", "branch"), ("branch", "even", "Even"),
                   ("branch", "odd", "Default"), ("even", "sub"), ("odd", "sub")]),
    c.chain("pc_sub", nodes=[c.step("double", f"{S}/double.py")]),
    c.chain("pc_main", root=True,
            nodes=[c.loop("loop", "chain:pc_iter", "msg.stop != true"), c.step("final", f"{S}/final.py")],
            edges=[("loop", "final")]),
]

if __name__ == "__main__":
    for ch in CHAINS:
        print(c.write(ch, HERE / "chains"))

"""Spike step: decide the branch and whether to stop; a controller would also advance the episode."""
from pclib import trace


def main(inp):
    d = dict(inp.data, passed=inp.data["count"] % 2 == 0, stop=inp.data["count"] >= 3)
    d["trail"] = d["trail"] + ["judge"]
    trace("controller.decision", slot="judge", payload={"stop": d["stop"]})
    return {"data": d}

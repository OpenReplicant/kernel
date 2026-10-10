"""Spike step: count iterations. Like every pclib step, a main(inp) returning {"data": ...}."""
from pclib import trace


def main(inp):
    d = dict(inp.data, count=inp.data.get("count", 0) + 1)
    d["trail"] = d.get("trail", []) + ["inc"]
    trace("slot.emit", slot="inc", port="output", payload={"count": d["count"]})
    return {"data": d}

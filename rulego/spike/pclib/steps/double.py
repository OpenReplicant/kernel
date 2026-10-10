"""Spike step, run inside a sub-chain."""


def main(inp):
    return {"data": dict(inp.data, doubled=inp.data["count"] * 2, trail=inp.data["trail"] + ["double"])}

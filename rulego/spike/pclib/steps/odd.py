"""Spike step: mark the path taken."""


def main(inp):
    return {"data": dict(inp.data, trail=inp.data["trail"] + ["odd"])}

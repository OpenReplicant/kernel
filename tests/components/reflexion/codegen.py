"""Stub Policy: 'writes' a solution whose quality depends on how many reflections it saw."""
from pclib import trace


def main(inp):
    ctx = inp.data["inputs"]["start"]
    n = len(ctx.get("reflections", []))
    trace("llm.request", slot="actor", payload={"messages": [{"role": "user", "content": ctx["prompt"]}]})
    return {"data": {"outputs": {"output": f"solution after {n} reflections"}}}

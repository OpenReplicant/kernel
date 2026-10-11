"""Stub Memory (episodic): keeps every item, reads back the last max_items."""


def main(inp):
    items = (inp.data["state"] or []) + [inp.data["inputs"]["write"]]
    window = items[-int(inp.data["params"].get("max_items", 3)):]
    return {"data": {"outputs": {"written": len(items), "read": window}, "state": items}}

"""Stub ContextBuilder: puts remembered reflections ahead of the task in the actor's prompt."""


def main(inp):
    items = inp.data["inputs"].get("input") or []
    header = inp.data["params"].get("header", "Reflections:")
    prompt = "\n".join([header, *items, "Write the solution."]) if items else "Write the solution."
    return {"data": {"outputs": {"output": {"prompt": prompt, "reflections": items}}}}

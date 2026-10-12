"""coder: a task {task, spec, tests} -> a model call asking for solution.py."""

SYSTEM = ("You write Python. Reply with the complete contents of solution.py in one ```python code block, "
          "and nothing else.")


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    call = {"role": "writer", "system": SYSTEM, "prompt": msg["spec"], "inputs": {"task": msg["task"]}}
    return {"msg": {"model_call": call, "task": msg}, "metadata": metadata, "msgType": msgType}

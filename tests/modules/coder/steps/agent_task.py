"""coder: a task {task, spec, tests} -> an agent run: write solution.py, test it in the sandbox."""

SYSTEM = ("You write Python. Your only tool runs code in an isolated sandbox (no network); use it to test "
          "your code before you answer. Finish with the complete contents of solution.py in one ```python "
          "code block.")


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    run = {"role": "agent", "system": SYSTEM, "task": msg["spec"], "inputs": {"task": msg["task"]}}
    return {"msg": {"agent_run": run, "task": msg}, "metadata": metadata, "msgType": msgType}

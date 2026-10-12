"""coder: the model's reply (a model call's or an agent run's) -> a sandbox run of the task's
tests against the extracted code, citing the call or run that wrote it."""
import re


def extract(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    return blocks[0] if blocks else text


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    task = msg["task"]
    reply, by = (msg["model_reply"], "model_call") if "model_reply" in msg else (msg["agent_result"], "agent_run")
    run = {"command": ["python", "test_solution.py"], "files": {"solution.py": extract(reply["text"]),
                                                               "test_solution.py": task["tests"]},
           "image": "python", "timeout_s": 10, "inputs": {"task": task["task"], by: reply["source"]}}
    return {"msg": {"sandbox_run": run, "task": task}, "metadata": metadata, "msgType": msgType}

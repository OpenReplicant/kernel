"""coder: the model's reply -> a sandbox run of the task's tests against the extracted code."""
import re


def extract(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    return blocks[0] if blocks else text


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    task, reply = msg["task"], msg["model_reply"]
    run = {"command": ["python", "test_solution.py"], "files": {"solution.py": extract(reply["text"]),
                                                               "test_solution.py": task["tests"]},
           "image": "python", "timeout_s": 10, "inputs": {"task": task["task"], "model_call": reply["source"]}}
    return {"msg": {"sandbox_run": run, "task": task}, "metadata": metadata, "msgType": msgType}

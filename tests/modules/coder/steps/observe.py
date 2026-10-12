"""coder: the sandbox result -> an observed claim that the task's tests passed (or not)."""


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    r = msg["sandbox_result"]
    passed = r["exit_code"] == 0 and not r["timed_out"]
    write = {"source": {"id": r["source"]},
             "things": [{"ref": "task", "label": "task", "key": msg["task"]["task"]}],
             "claims": [{"subject": "task", "predicate": "coder.passed", "value": passed, "method": "observed",
                         "confidence": 1.0, "span": "$.result.exit_code", "replaces": "current"}]}
    return {"msg": {**write, "sandbox_result": r}, "metadata": metadata, "msgType": msgType}

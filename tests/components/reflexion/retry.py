"""Stub Controller: retry until max_trials attempts, then stop."""


def main(inp):
    trials = (inp.data["state"] or 0) + 1
    port = "stop" if trials >= int(inp.data["params"].get("max_trials", 5)) else "retry"
    return {"data": {"outputs": {port: {"trials": trials}}, "state": trials}}

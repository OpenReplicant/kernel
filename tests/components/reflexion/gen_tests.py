"""Stub Evaluator (output): 'generates' a fixed test list once per task."""


def main(inp):
    return {"data": {"outputs": {"tests": ["assert f(1) == 1", "assert f(2) == 4"]}}}

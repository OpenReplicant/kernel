"""Stub Evaluator (trajectory): passes once the solution was written with two reflections."""


def main(inp):
    solution, tests = inp.data["inputs"]["run"], inp.data["inputs"].get("tests") or []
    n = int(solution.split()[2])
    verdict = {"passed": n >= 2, "detail": f"{len(tests)} tests, solution saw {n} reflections"}
    return {"data": {"outputs": {"pass" if verdict["passed"] else "fail": verdict}}}

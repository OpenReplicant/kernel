"""T4 gate: runs eval/validator against validator.core (no database needed).

Run: python modules/validator-shacl/tests/test_eval.py      (needs rdflib, pyshacl, jsonschema, pyyaml)
"""
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from validator.core import validate  # noqa: E402

SET = HERE.parents[2] / "eval" / "validator"


def reason_found(reason: str, report: dict) -> bool:
    if reason.startswith("schema: "):
        prefix = reason[len("schema: "):]
        return any(e["path"] == prefix or e["path"].startswith(prefix + "/") for e in report["schema_errors"])
    if reason.startswith("shape: "):
        return any(v["path"] == reason[len("shape: "):] for v in report["shape_violations"])
    return any(v["message"] == reason for v in report["shape_violations"])


def run() -> tuple[int, list[str]]:
    cases = sorted((SET / "cases").glob("*.yaml"))
    failures = []
    for path in cases:
        case = yaml.safe_load(path.read_text())
        cs = json.loads((SET / case["input"]["change_set"]).read_text())
        ok, report = validate(cs, [tuple(t) for t in case["input"].get("context", [])])
        want = case["expect"]
        got = "valid" if ok else "invalid"
        if got != want["outcome"]:
            failures.append(f"{case['id']}: expected {want['outcome']}, got {got}: {json.dumps(report)}")
        elif not ok and not reason_found(want["reason"], report):
            failures.append(f"{case['id']}: reason {want['reason']!r} not in report: {json.dumps(report)}")
    return len(cases), failures


if __name__ == "__main__":
    n, failures = run()
    for f in failures:
        print("FAIL", f)
    print(f"validator eval: {n - len(failures)}/{n} cases passed (accuracy {(n - len(failures)) / n:.2f})")
    sys.exit(1 if failures else 0)

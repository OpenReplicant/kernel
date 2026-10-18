"""T7 gate: runs eval/belief-v0 against belief_corroboration.model (no database needed).

Run: python modules/belief-corroboration/tests/test_eval.py      (needs pyyaml)
"""
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from belief_corroboration.model import Claim, score  # noqa: E402

SET = HERE.parents[2] / "eval" / "belief-v0"


def when(v):
    return datetime.fromisoformat(v) if v else None


def run():
    cases = sorted((SET / "cases").glob("*.yaml"))
    failures = []
    for path in cases:
        case = yaml.safe_load(path.read_text())
        claims = [Claim(c["id"], c["key"], c["subject"], c["predicate"], c["object"], c["agent"],
                        frozenset(c["sources"]), when(c.get("valid_from")), when(c.get("valid_to")),
                        c.get("deprecated", False), c.get("entity"), tuple(c.get("refuted_by", ()))) for c in case["input"]["claims"]]
        got = {k: v for k, (v, _) in score(claims, set(case["input"]["functional"])).items()}
        if got != case["expect"]["beliefs"]:
            failures.append(f"{case['id']}: expected {case['expect']['beliefs']}, got {got}")
    return len(cases), failures


if __name__ == "__main__":
    n, failures = run()
    for f in failures:
        print("FAIL", f)
    print(f"belief eval: {n - len(failures)}/{n} cases passed (accuracy {(n - len(failures)) / n:.2f})")
    sys.exit(1 if failures else 0)

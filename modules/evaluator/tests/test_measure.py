"""Evaluator checks (D-71): a run file gives one DQV measurement per score, nothing else.

  python modules/evaluator/tests/test_measure.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluator.measure import DQV, measurements  # noqa: E402

checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


run = {"stage": "verify", "split": "dev", "models": {"m/x:free": 300}, "claims": 300, "unparsed": False,
       "claim_accuracy_with_kernel": 0.883, "abstract_label_only": {"f1": 0.7, "p/r": 0.5}, "per_case": [1, 2]}
ops = measurements(run, "scifact", "2026-10-17-verify-x", "a" * 64)
m = {op["qualifiers"][DQV + "isMeasurementOf"]: op for op in ops if op["predicate"] == DQV + "value"}
check("one measurement per number; models' answer counts, booleans and lists left out",
      sorted(m) == ["urn:kk:metric:scifact:abstract_label_only.f1", "urn:kk:metric:scifact:abstract_label_only.p-r",
                    "urn:kk:metric:scifact:claim_accuracy_with_kernel", "urn:kk:metric:scifact:claims"])
acc = m["urn:kk:metric:scifact:claim_accuracy_with_kernel"]
check("the run is the subject, the value typed, the eval set as dqv:computedOn",
      acc["subject"] == "urn:kk:eval:scifact:2026-10-17-verify-x"
      and acc["object"]["value"] == {"@value": 0.883, "@type": "http://www.w3.org/2001/XMLSchema#decimal"}
      and acc["qualifiers"][DQV + "computedOn"] == "urn:kk:dataset:scifact:dev" and acc["evidence_type"] == "observed")
check("each score references the run file and its place in it (JSON pointer)",
      m["urn:kk:metric:scifact:abstract_label_only.p-r"]["references"][0]["span"]["value"] == "/abstract_label_only/p~1r"
      and acc["references"][0]["artifact"] == "a" * 64)
check("the run is labelled with the models that answered", "m/x:free" in ops[0]["object"]["value"]["@value"])
check("rcaeval's dataset key names the eval set",
      measurements({"dataset": "re1-ob", "avg@5_mean": 0.9}, "rcaeval", "r", "b" * 64)[1]["qualifiers"][DQV + "computedOn"]
      == "urn:kk:dataset:rcaeval:re1-ob")
print(f"evaluator checks passed ({sum(checks)}/{len(checks)})" if all(checks) else "evaluator checks FAILED")
sys.exit(0 if all(checks) else 1)

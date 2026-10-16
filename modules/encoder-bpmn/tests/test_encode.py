"""M2-T2 unit checks: the sample BPMN gives the expected documented process, with task IRIs equal to
the event-log encoder's step IRIs."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/encoder-bpmn"), str(ROOT / "modules/encoder-eventlog"), str(ROOT)]
from bpmn_encoder.encode import BBO, CONTAINER, LABEL, NEXT, RDF_TYPE, RESOURCE, encode  # noqa: E402
from eventlog_encoder.encode import read_csv, step_iri  # noqa: E402

P = "urn:kk:process:order-to-cash"
EX = ROOT / "examples/order-to-cash"
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


ops = encode((EX / "order-to-cash.bpmn").read_bytes(), P, "0" * 64)
types = Counter(o["object"]["entity"][len(BBO):] for o in ops if o["predicate"] == RDF_TYPE)
check(types == Counter({"Process": 1, "UserTask": 2, "ServiceTask": 2, "ManualTask": 1, "Task": 1,
                        "ExclusiveGateway": 1, "StartEvent": 1, "EndEvent": 2, "Role": 4}),
      "process, 6 tasks by kind, 1 gateway, 3 events, 4 lanes")
flows = [(o["subject"].rsplit(":", 1)[1], o["object"]["entity"].rsplit(":", 1)[1], o.get("qualifiers"))
         for o in ops if o["predicate"] == NEXT]
check(len(flows) == 9, "9 sequence flows")
check(("credit_ok", "ship-goods", {LABEL: "approved"}) in flows, "a gateway branch keeps its name as a qualifier")
check(("ship-goods", "send-invoice", None) in flows, "task to task flows have no qualifier (they meet observed edges)")
log_steps = {step_iri(P, e["activity"]) for e in read_csv((EX / "order-to-cash.csv").read_bytes(),
                                                          "case_id", "activity", "timestamp", "resource")}
tasks = {o["subject"] for o in ops if o["predicate"] == RDF_TYPE and ":step:" in o["subject"]}
check(tasks == log_steps, "BPMN tasks and log activities share step IRIs")
check(all(o["evidence_type"] == "deterministic" and o["references"][0]["span"]["type"] == "FragmentSelector"
          for o in ops), "deterministic, each referencing its BPMN element id")
check(sum(o["predicate"] == CONTAINER for o in ops) == 10, "every flow node is in the process")
check(sum(o["predicate"] == RESOURCE for o in ops) == 6, "every task has its lane's role")
try:
    encode(b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"/>', P, "0" * 64)
    check(False, "a model without a process is refused")
except ValueError:
    check(True, "a model without a process is refused")
print(f"M2-T2 BPMN encoder checks passed ({ok}/{ok})")

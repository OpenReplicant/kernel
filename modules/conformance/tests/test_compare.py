"""M2-T3 unit checks: the sample BPMN, log and written limit give exactly the planted discrepancies,
each derived from the observed and documented claims it compares."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / f"modules/{m}") for m in ("conformance", "encoder-bpmn", "encoder-eventlog")]
from bpmn_encoder.encode import encode as encode_bpmn  # noqa: E402
from conformance.compare import BBO, OBSERVED_PROPERTY, PREV, discrepancies  # noqa: E402
from eventlog_encoder.encode import encode as encode_log, read_csv  # noqa: E402

P = "urn:kk:process:order-to-cash"
EX = ROOT / "examples/order-to-cash"
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def rows(ops, start):
    out = []
    for i, op in enumerate(ops):
        if op["op"] == "add_claim":
            o = op["object"]
            out.append({"id": f"{start + i}", "subject": op["subject"], "predicate": op["predicate"],
                        "object_entity": o.get("entity"), "object_value": o.get("value"),
                        "qualifiers": op.get("qualifiers", {}), "evidence_type": op["evidence_type"]})
    return out


step = lambda n: f"{P}:step:{n}"
documented = rows(encode_bpmn((EX / "order-to-cash.bpmn").read_bytes(), P, "0" * 64), 1000)
observed = rows(encode_log(read_csv((EX / "order-to-cash.csv").read_bytes(), "case_id", "activity", "timestamp",
                                    "resource"), P, "week", "1" * 64), 2000)
limit = rows([{"op": "add_claim", "subject": step("send-invoice"), "predicate": BBO + "maxValue",
               "object": {"value": {"@value": 172800}}, "evidence_type": "extracted",
               "qualifiers": {OBSERVED_PROPERTY: "urn:kk:property:wait-seconds", PREV: step("ship-goods")}}], 3000)

check(discrepancies(observed) == [], "nothing documented: nothing to compare")
check(discrepancies(documented) == [], "nothing observed: nothing to report")
found = discrepancies(documented + observed + limit)
got = sorted((d["kind"].rsplit(":", 1)[1], *[t.rsplit(":", 1)[1] for t in d["targets"]]) for d in found)
check(got == sorted([("rework", "check-credit"),
                     ("skip", "check-credit", "receive-order", "ship-goods"),
                     ("skip", "check-credit", "send-invoice", "ship-goods"),
                     ("swap", "send-invoice", "ship-goods"),
                     ("skip", "receive-payment", "send-invoice", "ship-goods"),
                     ("limit", "send-invoice", "ship-goods")]), f"the planted discrepancies, nothing else: {got}")
by = {(d["kind"].rsplit(":", 1)[1], tuple(d["targets"])): d for d in found}
skip = by[("skip", (step("check-credit"), step("receive-order"), step("ship-goods")))]
check("4 case(s)" in skip["text"] and "without Check credit" in skip["text"], f"readable: {skip['text']}")
ids = set(skip["premises"])
check(any(i.startswith("2") for i in ids) and any(i.startswith("1") for i in ids),
      "a discrepancy is derived from observed and documented claims")
lim = next(d for d in found if d["kind"].endswith("limit"))
check("3000" in lim["premises"] and "48 h" in lim["text"], f"the limit cites the documented limit: {lim['text']}")
check(len({d["iri"] for d in found}) == len(found) and found == discrepancies(documented + observed + limit),
      "stable IRIs: the same input gives the same discrepancies")
print(f"M2-T3 conformance checks passed ({ok}/{ok})")

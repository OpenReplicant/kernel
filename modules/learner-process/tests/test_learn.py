"""D-76 unit checks on the order-to-cash sample: a confirmed, frequent deviation becomes a proposal
derived from the confirmed discrepancy; an unconfirmed or rare one does not; once the proposal's path is
in the documented process, conformance no longer finds the discrepancy."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / f"modules/{m}") for m in ("learner-process", "conformance", "encoder-bpmn", "encoder-eventlog")]
from bpmn_encoder.encode import encode as encode_bpmn  # noqa: E402
from conformance.compare import discrepancies  # noqa: E402
from eventlog_encoder.encode import encode as encode_log, read_csv  # noqa: E402
from process_learner.learn import ADD_PATH, NEXT, proposals  # noqa: E402

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
    return [{"id": f"{start + i}", "subject": op["subject"], "predicate": op["predicate"],
             "object_entity": op["object"].get("entity"), "object_value": op["object"].get("value"),
             "qualifiers": op.get("qualifiers", {}), "evidence_type": op["evidence_type"]}
            for i, op in enumerate(ops) if op["op"] == "add_claim"]


claims = (rows(encode_bpmn((EX / "order-to-cash.bpmn").read_bytes(), P, "0" * 64), 1000)
          + rows(encode_log(read_csv((EX / "order-to-cash.csv").read_bytes(), "case_id", "activity", "timestamp",
                                     "resource"), P, "week", "1" * 64), 2000))
found = discrepancies(claims)
by = {d["kind"].rsplit(":", 1)[1] + ":" + "|".join(t.rsplit(":", 1)[1] for t in d["targets"]): d for d in found}
swap = by["swap:send-invoice|ship-goods"]
rework = by["rework:check-credit"]
labels = {c["subject"]: c["object_value"]["@value"] for c in claims if c["predicate"].endswith("#label")}

check(proposals(found, {}, labels) == [], "nothing confirmed: nothing proposed")
confirmed = {swap["iri"]: ["9001", "9002"], rework["iri"]: ["9003"]}
got = proposals(found, confirmed, labels, share=0.05)
check(len(got) == 2 and all(c["lane"] == "review" for c in got), f"each confirmed, frequent deviation is a proposal for review: {len(got)}")
cs = next(c for c in got if c["idempotency_key"] == f"learn:{swap['iri']}")
path = [o for o in cs["ops"] if o["predicate"] == NEXT]
check(len(path) == 1 and [path[0]["subject"], path[0]["object"]["entity"]] == swap["edge"]
      and path[0]["evidence_type"] == "inferred", "the proposal adds the observed path to the documented process")
check(all(o["derived_from"] == ["9001", "9002"] for o in cs["ops"]), "every claim is derived from the confirmed discrepancy")
body = next(o for o in cs["ops"] if o["predicate"].endswith("bodyValue"))["object"]["value"]["@value"]
check(any(o["object"].get("entity") == ADD_PATH for o in cs["ops"]) and "of 40 cases" in body and "Ship goods" in body,
      f"a reviewer reads why: {body}")
check(proposals(found, confirmed, labels, share=0.5) == [], "a deviation in a small share of cases is not proposed")

approved = claims + rows(path, 5000)
after = {d["iri"] for d in discrepancies(approved)}
check(swap["iri"] not in after and rework["iri"] in after,
      "approved, the path is documented: conformance no longer finds that discrepancy, and still finds the others")
print(f"D-76 process learner checks passed ({ok}/{ok})")

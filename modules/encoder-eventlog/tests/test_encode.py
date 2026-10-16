"""M2-T1 unit checks: the sample log gives the expected steps, edge counts and windows; XES reads the
same way; re-encoding a changed log supersedes by (subject, predicate, qualifiers, window start)."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/encoder-eventlog"), str(ROOT)]
from kk.changes import supersede  # noqa: E402
from eventlog_encoder.encode import NEXT, OBSERVED_PROPERTY, PREV, RESULT, encode, read_csv, read_xes  # noqa: E402

P = "urn:kk:process:order-to-cash"
LOG = (ROOT / "examples/order-to-cash/order-to-cash.csv").read_bytes()
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def step(name):
    return f"{P}:step:{name}"


def obs(ops, subject, prop, **q):
    return [o["object"]["value"]["@value"] for o in ops if o["predicate"] == RESULT and o["subject"] == subject
            and o["qualifiers"][OBSERVED_PROPERTY] == "urn:kk:property:" + prop
            and all(o["qualifiers"].get(k) == v for k, v in q.items()) and len(o["qualifiers"]) == 1 + len(q)]


events = read_csv(LOG, "case_id", "activity", "timestamp", "resource")
ops = encode(events, P, "all", "0" * 64)
check(len(events) == 188, "188 events read")
labels = {o["subject"] for o in ops if o["predicate"].endswith("#label")}
check(len(labels) == 6 and step("check-credit") in labels, "6 steps, IRIs from activity names")
check(obs(ops, P, "case-count") == [40], "40 cases")
check(obs(ops, step("check-credit"), "case-count") == [36] and obs(ops, step("check-credit"), "event-count") == [38],
      "check credit: 36 cases, 38 events (rework)")
edges = Counter((o["subject"].rsplit(":", 1)[1], o["object"]["entity"].rsplit(":", 1)[1])
                for o in ops if o["predicate"] == NEXT)
check(len(edges) == 10, "10 directly-follows edges observed")
expected = {("receive-order", "check-credit"): 36, ("receive-order", "ship-goods"): 4,
            ("check-credit", "ship-goods"): 28, ("check-credit", "check-credit"): 2,
            ("check-credit", "reject-order"): 5, ("check-credit", "send-invoice"): 3,
            ("ship-goods", "send-invoice"): 32, ("send-invoice", "ship-goods"): 3,
            ("send-invoice", "receive-payment"): 32, ("ship-goods", "receive-payment"): 3}
got = {(a, b): obs(ops, step(b), "directly-follows-count", **{PREV: step(a)})[0] for a, b in edges}
check(got == expected, "directly-follows counts match the planted variants")
check(all(o["evidence_type"] == "observed" and o["references"] == [{"artifact": "0" * 64}]
          for o in ops if o["predicate"] in (RESULT, NEXT)), "observations are observed claims referencing the log")
check(obs(ops, step("ship-goods"), "event-count", **{"https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_resource":
                                                     "urn:kk:resource:warehouse-1"}) == [35], "per-resource counts")

weekly = encode(events, P, "week", "0" * 64)
windows = sorted({o["valid_from"] for o in weekly if "valid_from" in o})
check(len(windows) == 3 and windows[0].startswith("2026-09-07"), "weekly: three windows starting on Mondays")
check(sum(v for o in weekly if o["subject"] == P for v in [o["object"]["value"]["@value"]]) == 40,
      "weekly case counts add up to 40")
slow = [o for o in weekly if o["subject"] == step("send-invoice") and o["predicate"] == RESULT
        and o["qualifiers"][OBSERVED_PROPERTY].endswith("wait-seconds-mean")
        and o["qualifiers"].get(PREV) == step("ship-goods")]
waits = {o["valid_from"][:10]: o["object"]["value"]["@value"] for o in slow}
check(waits["2026-09-21"] > 4 * waits["2026-09-07"], "week 3 ship → invoice wait is planted slow")

xes = """<?xml version="1.0" encoding="UTF-8"?><log xmlns="http://www.xes-standard.org/">
<trace><string key="concept:name" value="c1"/>
 <event><string key="concept:name" value="A"/><date key="time:timestamp" value="2026-01-01T10:00:00+00:00"/></event>
 <event><string key="concept:name" value="B"/><string key="org:resource" value="r1"/><date key="time:timestamp" value="2026-01-01T11:00:00+00:00"/></event>
</trace></log>""".encode()
xops = encode(read_xes(xes), P, "all", "1" * 64)
check(obs(xops, step("b"), "wait-seconds-mean", **{PREV: step("a")}) == [3600], "XES: events, order and waits")

# Re-ingest: a changed log supersedes matching claims and retracts the rest.
cs = {"ops": [dict(o) for o in ops]}
first = next(o for o in ops if o["subject"] == P)
prior = [{"id": 1, "subject": first["subject"], "predicate": first["predicate"], "qualifiers": first["qualifiers"],
          "valid_from": first["valid_from"], "status": "submitted"},
         {"id": 2, "subject": step("gone"), "predicate": RESULT, "qualifiers": {}, "valid_from": None, "status": "preferred"}]
supersede(cs, prior)
s1, s2 = cs["ops"][-2:]
check(s1["reason"] == "superseded" and s1["replaced_by"].startswith("c") and s2["reason"] == "retracted",
      "re-ingest supersedes the same observation and retracts the rest")
print(f"M2-T1 encoder checks passed ({ok}/{ok})")

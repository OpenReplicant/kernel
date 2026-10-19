"""M6-T12 unit checks (D-89): a claim built on a withdrawn premise is flagged once, with the premises
named; restored when its premises are current again; nothing said about claims already in step."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from truth_maintenance.plan import verdicts  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


out = verdicts({"c": ["p2", "p1"]}, {})
check(out == [("c", "inconclusive", {"check": "premises current", "withdrawn": ["p1", "p2"]})],
      "a claim with withdrawn premises is flagged inconclusive, naming them")
check(verdicts({"c": ["p1"]}, {"c": "inconclusive"}) == [], "a flagged claim is not flagged again")
check(verdicts({}, {"c": "inconclusive"}) == [("c", "supported", {"check": "premises current", "withdrawn": []})],
      "its premises current again: restored")
check(verdicts({}, {"c": "supported"}) == [], "a restored claim in step: nothing")
check(verdicts({"c": ["p1"]}, {"c": "supported"})[0][1] == "inconclusive", "restored, then withdrawn again: flagged again")
print(f"M6-T12 truth maintenance checks passed ({ok}/{ok})")

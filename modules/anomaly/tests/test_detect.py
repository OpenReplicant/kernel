"""M2-T6 unit checks for the anomaly processor."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/anomaly")]
from anomaly.detect import MAX, PROP, anomalies, change_set  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


LAT = "urn:kk:property:latency-seconds-mean"
obs = lambda i, v, prop=LAT: {"id": f"o{i}", "subject": "urn:kk:node:shop", "qualifiers": {PROP: prop},
                              "object_value": {"@value": v}, "valid_from": f"2026-10-16T10:{i:02d}:00+00:00",
                              "valid_to": f"2026-10-16T10:{i + 1:02d}:00+00:00"}
steady = [obs(i, 0.012 + 0.001 * (i % 3)) for i in range(10)]
check(anomalies(steady, []) == [], "steady observations, no limit: no anomaly")
limit = [{"id": "L", "subject": "urn:kk:node:shop", "predicate": MAX, "qualifiers": {PROP: LAT}, "object_value": {"@value": 0.2}}]
check(anomalies(steady, limit) == [], "under the documented limit: no anomaly")
found = anomalies(steady + [obs(10, 1.8)], limit)
kinds = sorted(a["kind"].rsplit(":", 1)[1] for a in found)
check(kinds == ["limit", "zscore"], f"a spike is over the limit and unusual: {kinds}")
lim = next(a for a in found if a["kind"].endswith("limit"))
check(lim["premises"] == ["o10", "L"] and lim["valid_from"].startswith("2026-10-16T10:10"),
      "derived from the observation and the limit, valid for its window")
check(anomalies(steady + [obs(10, 1.8)], limit) == found, "stable: the same input gives the same anomalies")
cs = change_set(lim, "urn:kk:agent:anomaly", "urn:kk:scope:default")
check(all(op["derived_from"] == ["o10", "L"] and op["evidence_type"] == "inferred" for op in cs["ops"])
      and "lane" not in cs, "every claim inferred with its premises; the auto lane")
check(anomalies([obs(i, 0.5, "urn:kk:property:request-rate") for i in range(3)], limit) == [],
      "a limit applies to its own property only")
print(f"M2-T6 anomaly checks passed ({ok}/{ok})")

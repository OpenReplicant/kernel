"""M2-T5 unit checks: Prometheus answers become one observation per service, property and window."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/observer-prometheus")]
from observer_prometheus.observe import SOSA, ops, query_url, window  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


check(window(1000.5, 60) == (900, 960), "the last complete window, aligned to the clock")
url = query_url("http://prometheus:9090/", "sum by (job) (up)", 960)
check(url == "http://prometheus:9090/api/v1/query?query=sum+by+%28job%29+%28up%29&time=960", "the exact query URL")
vector = lambda *rows: {"status": "success", "data": {"resultType": "vector",
                                                     "result": [{"metric": m, "value": [960, v]} for m, v in rows]}}
out = ops([("urn:kk:property:request-rate", url, vector(({"job": "shop"}, "12.5"), ({"job": "prometheus"}, "0.2"))),
           ("urn:kk:property:latency-seconds-mean", url, vector(({"job": "shop"}, "NaN"), ({}, "1"))),
           ("urn:kk:property:replicas-up", url, {"status": "error"})], 900, 960)
check(len(out) == 2, "NaN, unlabelled series and failed queries are skipped")
shop = out[0] if out[0]["subject"].endswith("shop") else out[1]
check(shop["subject"] == "urn:kk:node:shop" and shop["predicate"] == SOSA + "hasSimpleResult"
      and shop["object"]["value"]["@value"] == 12.5
      and shop["qualifiers"] == {SOSA + "observedProperty": "urn:kk:property:request-rate"}, "subject, property, value")
check(shop["valid_from"].startswith("1970-01-01T00:15:00") and shop["valid_to"].startswith("1970-01-01T00:16:00")
      and shop["evidence_type"] == "observed" and shop["references"] == [{"source_uri": url}],
      "valid for the window, observed, referenced by the query")
print(f"M2-T5 observer checks passed ({ok}/{ok})")

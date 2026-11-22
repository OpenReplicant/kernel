"""D-119 unit checks: KPIs on a tiny hand-made log (exact values), targets marking met and missed, a changed mark
not changing a claim's identity, and a log without costs or resources giving no cost or handoff KPI."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/process-kpis"), str(ROOT / "modules/encoder-eventlog"), str(ROOT)]
from kk.changes import same  # noqa: E402
from process_kpis.kpis import MAX, MIN, OBSERVED_PROPERTY, PROPERTY, STATUS, compute, mark, ops, read_cases  # noqa: E402

LOG = b"""case_id,activity,timestamp,resource,cost
A,Receive,2026-01-05T09:00:00Z,r1,10
A,Check,2026-01-05T10:00:00Z,r2,5
A,Ship,2026-01-05T12:00:00Z,r1,20
B,Receive,2026-01-05T09:00:00Z,r1,10
B,Check,2026-01-05T09:30:00Z,r1,10
B,Check,2026-01-05T10:00:00Z,r1,10
B,Ship,2026-01-05T11:00:00Z,r1,10
C,Receive,2026-01-05T08:00:00Z,r1,10
C,Ship,2026-01-05T08:30:00Z,r2,10
D,Receive,2026-01-05T09:00:00Z,r1,10
D,Ship,2026-01-05T17:00:00Z,r2,20
E,Receive,2026-01-06T09:00:00Z,r1,2
E,Ship,2026-01-06T09:10:00Z,r1,3
"""
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


days = compute(read_cases(LOG), "day")
(d1, d2) = [v for _, v in sorted(days.items())]
check(d1 == {"cases-in-window": 4, "cycle-time-median-seconds": 9000, "cycle-time-p90-seconds": 28800,
             "rework-rate": 0.25, "throughput-per-day": 4.0, "cost-per-case": 31.25, "handoffs-per-case": 1.0},
      f"day 1: four cases, median 9000 s, p90 28800 s, rework 0.25, cost 31.25, handoffs 1.0 ({d1})")
check(d2 == {"cases-in-window": 1, "cycle-time-median-seconds": 600, "cycle-time-p90-seconds": 600,
             "rework-rate": 0.0, "throughput-per-day": 1.0, "cost-per-case": 5.0, "handoffs-per-case": 0.0},
      "day 2: one case of 600 s")
whole = list(compute(read_cases(LOG), "all").values())
check(len(whole) == 1 and whole[0]["cases-in-window"] == 5 and whole[0]["cycle-time-median-seconds"] == 7200,
      "the whole log is one window of five cases, median 7200 s")
check(len(compute(read_cases(LOG), "week")) == 1, "both days fall in one ISO week")

plain = read_cases(b"case_id,activity,timestamp\nA,x,2026-01-05T09:00:00Z\nA,y,2026-01-05T10:00:00Z\n")
(only,) = compute(plain, "day").values()
check("cost-per-case" not in only and "handoffs-per-case" not in only and only["cycle-time-median-seconds"] == 3600,
      "no cost column and no resources: no cost or handoff KPI")

check(mark("cycle-time-p90-seconds", 28800, []) == {}, "no target, no mark")
check(mark("cycle-time-p90-seconds", 28800, [{"kind": MAX, "value": 36000}]) == {STATUS: "met", MAX: 36000},
      "at or under a maximum: met")
check(mark("cycle-time-p90-seconds", 28800, [{"kind": MAX, "value": 7200}])[STATUS] == "missed", "over a maximum: missed")
check(mark("throughput-per-day", 4.0, [{"kind": MIN, "value": 5}])[STATUS] == "missed", "under a minimum: missed")
check(mark("rework-rate", 0.25, [{"kind": MIN, "value": 0.1}, {"kind": MAX, "value": 0.2}])[STATUS] == "missed",
      "a window must keep every bound")

refs = [{"artifact": "a" * 64}]
out = ops("urn:kk:process:p", days, {"rework-rate": [{"kind": MAX, "value": 0.2}]}, refs)
rework = [o for o in out if o["qualifiers"][OBSERVED_PROPERTY] == PROPERTY + "rework-rate"]
check(len(out) == 14 and all(o["references"] == refs and o["evidence_type"] == "observed" for o in out),
      "14 observations (7 per day), each observed and citing the log")
check([o["qualifiers"].get(STATUS) for o in rework] == ["missed", "met"], "rework 0.25 misses 0.2, 0.0 meets it")
unmarked = ops("urn:kk:process:p", days, {}, refs)
ident = lambda o: same(o["subject"], o["predicate"], o["qualifiers"], o["valid_from"], (STATUS, MAX, MIN))  # noqa: E731
check([ident(o) for o in out] == [ident(o) for o in unmarked], "a mark does not change which claim is replaced")
check(all(o["valid_from"] < o["valid_to"] for o in out), "each observation carries its window")
print(f"process-kpis: {ok} checks passed")

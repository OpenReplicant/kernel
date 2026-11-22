"""Event-log cases -> process KPIs per time window, marked against people's targets (D-119). Pure
functions; no database.

A case belongs to the window its first event falls in (as the event-log encoder places it). Per window:

  cases-in-window          number of cases
  cycle-time-median-seconds, cycle-time-p90-seconds   first event to last event, per case (p90: nearest rank)
  rework-rate              share of cases in which a step occurs more than once
  throughput-per-day       cases / length of the window in days
  cost-per-case            mean of the per-case sum of event costs   (only where the log has costs)
  handoffs-per-case        mean times consecutive events have different resources   (only where it names resources)

A target is a person's bound on a KPI: a bbo:maxValue or bbo:minValue claim on the process with the KPI as
sosa:observedProperty. An observation meets it when value <= max and value >= min; `mark` says so, and nothing more.
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from eventlog_encoder.encode import parse_time, traces_xes, window_of

SOSA = "http://www.w3.org/ns/sosa/"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RESULT, OBSERVED_PROPERTY = SOSA + "hasSimpleResult", SOSA + "observedProperty"
MAX, MIN = BBO + "maxValue", BBO + "minValue"
PROPERTY = "urn:kk:property:"
STATUS = PROPERTY + "target-status"
KPIS = ("cases-in-window", "cycle-time-median-seconds", "cycle-time-p90-seconds", "rework-rate",
        "throughput-per-day", "cost-per-case", "handoffs-per-case")
WINDOWS = ("all", "day", "week", "month")


# ------------------------------------------------------------------ reading
def read_cases(data: bytes, columns: dict | None = None) -> list[list[dict]]:
    """A log (CSV, XES or XES gzipped; sniffed) as cases, each a time-ordered list of events with case,
    activity, time, resource and cost (None when absent). `columns` renames the CSV's columns."""
    cols = {"case": "case_id", "activity": "activity", "timestamp": "timestamp", "resource": "resource",
            "cost": "cost", **(columns or {})}
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data.lstrip()[:1] == b"<":
        return [sorted(t, key=lambda e: e["time"]) for _, t in traces_xes(io.BytesIO(data))]
    rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    missing = {cols["case"], cols["activity"], cols["timestamp"]} - set(rows.fieldnames or [])
    if missing:
        raise ValueError(f"the log lacks the column(s) {sorted(missing)}")
    cases = defaultdict(list)
    for r in rows:
        if r[cols["case"]] and r[cols["activity"]] and r[cols["timestamp"]]:
            cost = (r.get(cols["cost"]) or "").strip()
            cases[r[cols["case"]]].append({"case": r[cols["case"]], "activity": r[cols["activity"]],
                                           "time": parse_time(r[cols["timestamp"]]),
                                           "resource": r.get(cols["resource"]) or None,
                                           "cost": float(cost) if cost else None})
    return [sorted(cases[k], key=lambda e: e["time"]) for k in sorted(cases)]


# ------------------------------------------------------------------ computing
def nearest_rank(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[max(1, math.ceil(q * len(s))) - 1]


def compute(cases: list[list[dict]], window: str = "day") -> dict[tuple[datetime, datetime], dict[str, int | float]]:
    """{(window start, window end): {kpi: value}} for the cases (see the module docstring)."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {WINDOWS}")
    cases = [c for c in cases if c]
    if not cases:
        return {}
    firsts = [c[0]["time"].astimezone(timezone.utc) for c in cases]
    lasts = [c[-1]["time"].astimezone(timezone.utc) for c in cases]
    first, last = min(firsts), max(lasts)
    groups = defaultdict(list)
    for c, t0 in zip(cases, firsts):
        groups[(first, last + timedelta(seconds=1)) if window == "all" else window_of(t0, window, first, last)].append(c)
    out = {}
    for (vf, vt), group in sorted(groups.items()):
        cycles = [(c[-1]["time"] - c[0]["time"]).total_seconds() for c in group]
        acts = [[e["activity"] for e in c] for c in group]
        costs = [sum(e["cost"] for e in c if e["cost"] is not None) for c in group if any(e["cost"] is not None for e in c)]
        hand = [sum(1 for a, b in zip(c, c[1:]) if a["resource"] and b["resource"] and a["resource"] != b["resource"])
                for c in group if any(e["resource"] for e in c)]
        kpi = {"cases-in-window": len(group),
               "cycle-time-median-seconds": round(statistics.median(cycles)),
               "cycle-time-p90-seconds": round(nearest_rank(cycles, 0.9)),
               "rework-rate": round(sum(len(set(a)) < len(a) for a in acts) / len(group), 4),
               "throughput-per-day": round(len(group) / ((vt - vf).total_seconds() / 86400), 4)}
        if costs:
            kpi["cost-per-case"] = round(sum(costs) / len(costs), 4)
        if hand:
            kpi["handoffs-per-case"] = round(sum(hand) / len(hand), 4)
        out[(vf, vt)] = kpi
    return out


def mark(prop: str, value: float, targets: list[dict]) -> dict:
    """The qualifiers a person's targets for `prop` put on an observation of `value`: {} without a target,
    else the status (met when every bound holds) and the bounds. targets: {kind: MAX|MIN, value}."""
    mine = [t for t in targets if t["value"] is not None]
    if not mine:
        return {}
    ok = all(value <= t["value"] if t["kind"] == MAX else value >= t["value"] for t in mine)
    return {STATUS: "met" if ok else "missed", **{t["kind"]: t["value"] for t in mine}}


def number(prop: str, v) -> dict:
    """The claim's object for a KPI value: integers for counts and seconds, decimals for the rest."""
    if prop in ("cases-in-window", "cycle-time-median-seconds", "cycle-time-p90-seconds"):
        return {"value": {"@value": int(v), "@type": XSD + "integer"}}
    return {"value": {"@value": v, "@type": XSD + "decimal"}}


def ops(process: str, window: dict, targets: dict, refs: list[dict]) -> list[dict]:
    """The add_claim ops for every window's KPIs: window is compute()'s result, targets {kpi: [target]}."""
    out = []
    for (vf, vt), kpi in sorted(window.items()):
        for prop, v in kpi.items():
            out.append({"op": "add_claim", "subject": process, "predicate": RESULT, "object": number(prop, v),
                        "qualifiers": {OBSERVED_PROPERTY: PROPERTY + prop, **mark(prop, v, targets.get(prop, []))},
                        "evidence_type": "observed", "references": refs,
                        "valid_from": vf.isoformat(), "valid_to": vt.isoformat()})
    return out

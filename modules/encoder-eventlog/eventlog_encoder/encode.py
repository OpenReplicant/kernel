"""Event log -> one change set of observed claims (D-48). Pure functions; no database.

Steps are `<process>:step:<slug of the activity name>`, the same IRIs the BPMN encoder gives tasks, so
documented and observed claims meet on the same entities. Per time window (cases are placed in the
window their first event falls in):

  <process>  sosa:hasSimpleResult n   {observedProperty: case-count}
  <step>     sosa:hasSimpleResult n   {observedProperty: case-count | event-count | start-count | end-count}
  <step>     sosa:hasSimpleResult n   {observedProperty: event-count, bbo:has_resource: <resource>}
  <a>        bbo:has_nextFlowNode <b>                                       (evidence observed)
  <b>        sosa:hasSimpleResult n   {observedProperty: directly-follows-count | wait-seconds-mean | -max,
                                       bbo:has_previousFlowNode: <a>}

plus, once, `<step> rdfs:label "<activity>"` and `<step> bbo:has_container <process>` (deterministic).
Every claim references the log artifact.
"""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
SOSA = "http://www.w3.org/ns/sosa/"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
NEXT, PREV, CONTAINER, RESOURCE = BBO + "has_nextFlowNode", BBO + "has_previousFlowNode", BBO + "has_container", BBO + "has_resource"
RESULT, OBSERVED_PROPERTY = SOSA + "hasSimpleResult", SOSA + "observedProperty"
PROPERTY = "urn:kk:property:"
WINDOWS = ("all", "day", "week", "month")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed"


def step_iri(process: str, activity: str) -> str:
    return f"{process}:step:{slug(activity)}"


def resource_iri(resource: str) -> str:
    return f"urn:kk:resource:{slug(resource)}"


def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ reading
def read_csv(data: bytes, case: str, activity: str, timestamp: str, resource: str | None) -> list[dict]:
    rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    missing = {case, activity, timestamp} - set(rows.fieldnames or [])
    if missing:
        raise ValueError(f"CSV lacks the column(s) {sorted(missing)}; name them with --case/--activity/--timestamp")
    return [{"case": r[case], "activity": r[activity], "time": parse_time(r[timestamp]),
             "resource": (r.get(resource) or None) if resource else None}
            for r in rows if r[case] and r[activity] and r[timestamp]]


def read_xes(data: bytes) -> list[dict]:
    """XES (IEEE 1849): trace concept:name, event concept:name, time:timestamp, org:resource.
    ElementTree resolves no external entities; expat limits entity expansion."""
    local = lambda tag: tag.rsplit("}", 1)[-1]
    events = []
    for trace in (e for e in ET.fromstring(data) if local(e.tag) == "trace"):
        attrs = lambda el: {a.get("key"): a.get("value") for a in el if local(a.tag) in ("string", "date")}
        case = attrs(trace).get("concept:name")
        for ev in (e for e in trace if local(e.tag) == "event"):
            a = attrs(ev)
            if case and a.get("concept:name") and a.get("time:timestamp"):
                events.append({"case": case, "activity": a["concept:name"], "time": parse_time(a["time:timestamp"]),
                               "resource": a.get("org:resource")})
    return events


# ------------------------------------------------------------------ windows
def window_of(t: datetime, kind: str, first: datetime, last: datetime) -> tuple[datetime, datetime]:
    t = t.astimezone(timezone.utc)
    if kind == "all":
        return first, last + timedelta(seconds=1)
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "day":
        return day, day + timedelta(days=1)
    if kind == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    start = day.replace(day=1)
    return start, (start + timedelta(days=32)).replace(day=1)


# ------------------------------------------------------------------ encoding
def integer(n: int) -> dict:
    return {"value": {"@value": int(n), "@type": XSD + "integer"}}


def encode(events: list[dict], process: str, window: str, sha: str) -> list[dict]:
    """The add_claim ops for one log (the caller adds the artifact op)."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {WINDOWS}")
    if not events:
        raise ValueError("the log has no events")
    ref = [{"artifact": sha}]
    cases: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        cases[e["case"]].append(e)
    for trace in cases.values():
        trace.sort(key=lambda e: e["time"])          # stable: equal timestamps keep file order
    first = min(e["time"] for e in events).astimezone(timezone.utc)
    last = max(e["time"] for e in events).astimezone(timezone.utc)

    ops: list[dict] = []
    for act in sorted({e["activity"] for e in events}):
        s = step_iri(process, act)
        ops.append({"op": "add_claim", "subject": s, "predicate": RDFS_LABEL, "object": {"value": {"@value": act}},
                    "evidence_type": "deterministic", "references": ref})
        ops.append({"op": "add_claim", "subject": s, "predicate": CONTAINER, "object": {"entity": process},
                    "evidence_type": "deterministic", "references": ref})

    by_window: dict[tuple, list[list[dict]]] = defaultdict(list)
    for case in sorted(cases):
        trace = cases[case]
        by_window[window_of(trace[0]["time"], window, first, last)].append(trace)

    for (vf, vt), traces in sorted(by_window.items()):
        valid = {"valid_from": vf.isoformat(), "valid_to": vt.isoformat()}

        def observe(subject: str, prop: str, value: dict, **quals) -> None:
            ops.append({"op": "add_claim", "subject": subject, "predicate": RESULT, "object": value,
                        "qualifiers": {OBSERVED_PROPERTY: PROPERTY + prop, **quals},
                        "evidence_type": "observed", "references": ref, **valid})

        events_n, cases_n, starts, ends, by_res = Counter(), Counter(), Counter(), Counter(), Counter()
        follows, waits, longest = Counter(), defaultdict(float), defaultdict(float)
        for trace in traces:
            acts = [e["activity"] for e in trace]
            events_n.update(acts)
            cases_n.update(set(acts))
            starts[acts[0]] += 1
            ends[acts[-1]] += 1
            by_res.update((e["activity"], e["resource"]) for e in trace if e["resource"])
            for a, b in zip(trace, trace[1:]):
                follows[(a["activity"], b["activity"])] += 1
                wait = (b["time"] - a["time"]).total_seconds()
                waits[(a["activity"], b["activity"])] += wait
                longest[(a["activity"], b["activity"])] = max(longest[(a["activity"], b["activity"])], wait)

        observe(process, "case-count", integer(len(traces)))
        for act in sorted(events_n):
            s = step_iri(process, act)
            observe(s, "case-count", integer(cases_n[act]))
            observe(s, "event-count", integer(events_n[act]))
            if starts[act]:
                observe(s, "start-count", integer(starts[act]))
            if ends[act]:
                observe(s, "end-count", integer(ends[act]))
        for (act, res), n in sorted(by_res.items()):
            observe(step_iri(process, act), "event-count", integer(n), **{RESOURCE: resource_iri(res)})
        for (a, b), n in sorted(follows.items()):
            sa, sb = step_iri(process, a), step_iri(process, b)
            ops.append({"op": "add_claim", "subject": sa, "predicate": NEXT, "object": {"entity": sb},
                        "evidence_type": "observed", "references": ref, **valid})
            observe(sb, "directly-follows-count", integer(n), **{PREV: sa})
            observe(sb, "wait-seconds-mean", integer(round(waits[(a, b)] / n)), **{PREV: sa})
            observe(sb, "wait-seconds-max", integer(longest[(a, b)]), **{PREV: sa})
    return ops


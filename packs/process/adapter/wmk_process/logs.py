"""Event logs as cases: CSV, XES and OCEL 2.0 JSON, read with the standard library.

A log becomes `Log`: cases by id, each with its attributes and its events in time order
(ties keep the order of the file). Only what the adapter maps is kept: the activity label,
the time, the role, the case's attributes. Resources (people) are dropped on purpose
(ADR 0027): their names are personal data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LogError(ValueError):
    """A log the adapter cannot read."""


@dataclass(frozen=True)
class Event:
    activity: str
    time: datetime
    role: str | None = None


@dataclass
class Case:
    id: str
    attrs: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


@dataclass
class Log:
    cases: dict[str, Case]
    format: str
    sha256: str
    name: str

    @property
    def events(self) -> int:
        return sum(len(c.events) for c in self.cases.values())

    def window(self) -> tuple[datetime, datetime]:
        times = [e.time for c in self.cases.values() for e in c.events]
        if not times:
            raise LogError(f"{self.name} has no events")
        return min(times), max(times)


@dataclass(frozen=True)
class Columns:
    """Where a CSV keeps what the adapter reads. Defaults are XES's standard names."""

    case: str = "case:concept:name"
    activity: str = "concept:name"
    timestamp: str = "time:timestamp"
    role: str | None = "org:role"
    lifecycle: str | None = "lifecycle:transition"
    # Case attributes: columns whose value is the same on every row of a case.
    attributes: tuple[str, ...] = ()


def value(text: str | None) -> Any:
    """A CSV or XES string as a number or boolean where it reads as one."""
    if text is None:
        return None
    s = text.strip()
    if s == "":
        return None
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def timestamp(text: str) -> datetime:
    """ISO 8601; a time without an offset is UTC."""
    s = text.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        t = datetime.fromisoformat(s)
    except ValueError as exc:
        raise LogError(f"{text!r} is not an ISO 8601 time") from exc
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _complete(lifecycle: str | None) -> bool:
    # Without lifecycle information every event counts; with it, only completions.
    return lifecycle is None or lifecycle.strip().lower() in ("", "complete")


def _finish(cases: dict[str, Case]) -> dict[str, Case]:
    for case in cases.values():
        indexed = list(enumerate(case.events))
        indexed.sort(key=lambda ie: (ie[1].time, ie[0]))
        case.events = [e for _, e in indexed]
    return dict(sorted(cases.items()))


def read_csv(path: Path, columns: Columns | None = None) -> Log:
    columns = columns or Columns()
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    header = reader.fieldnames or []
    for need in (columns.case, columns.activity, columns.timestamp):
        if need not in header:
            raise LogError(f"{path.name} has no column {need!r} (columns: {', '.join(header)})")
    cases: dict[str, Case] = {}
    for row in reader:
        if not _complete(row.get(columns.lifecycle) if columns.lifecycle else None):
            continue
        cid = (row[columns.case] or "").strip()
        if not cid:
            continue
        case = cases.setdefault(cid, Case(cid))
        for name in columns.attributes:
            v = value(row.get(name))
            if v is not None and name not in case.attrs:
                case.attrs[name] = v
        role = (row.get(columns.role) or "").strip() if columns.role else ""
        case.events.append(
            Event(row[columns.activity].strip(), timestamp(row[columns.timestamp]), role or None)
        )
    return Log(_finish(cases), "csv", hashlib.sha256(data).hexdigest(), path.name)


def _xes_value(el: ET.Element) -> Any:
    tag = el.tag.rsplit("}", 1)[-1]
    raw = el.get("value")
    if tag == "date" and raw:
        return timestamp(raw)
    if tag == "boolean" and raw is not None:
        return raw.strip().lower() == "true"
    if tag in ("int", "float"):
        return value(raw)
    return raw


def _attrs(el: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for child in el:
        key = child.get("key")
        if key is not None and child.tag.rsplit("}", 1)[-1] in (
            "string",
            "int",
            "float",
            "boolean",
            "date",
            "id",
        ):
            out[key] = _xes_value(child)
    return out


def read_xes(path: Path, attributes: Iterable[str] | None = None) -> Log:
    """An XES log: trace attributes become case attributes (all of them, or those named),
    events their activity (concept:name), time (time:timestamp) and role (org:role); only
    completions count when events carry lifecycle:transition."""
    data = path.read_bytes()
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LogError(f"{path.name} is not XML: {exc}") from exc
    wanted = set(attributes) if attributes is not None else None
    cases: dict[str, Case] = {}
    for trace in (el for el in root if el.tag.rsplit("}", 1)[-1] == "trace"):
        tattrs = _attrs(trace)
        cid = tattrs.pop("concept:name", None)
        if cid is None:
            raise LogError(f"{path.name}: a trace has no concept:name")
        case = cases.setdefault(str(cid), Case(str(cid)))
        case.attrs.update({k: v for k, v in tattrs.items() if wanted is None or k in wanted})
        for ev in (el for el in trace if el.tag.rsplit("}", 1)[-1] == "event"):
            eattrs = _attrs(ev)
            if not _complete(eattrs.get("lifecycle:transition")):
                continue
            if "concept:name" not in eattrs or not isinstance(eattrs.get("time:timestamp"), datetime):
                raise LogError(f"{path.name}: an event of trace {cid} lacks concept:name or time:timestamp")
            role = eattrs.get("org:role")
            case.events.append(
                Event(str(eattrs["concept:name"]), eattrs["time:timestamp"], str(role) if role else None)
            )
    return Log(_finish(cases), "xes", hashlib.sha256(data).hexdigest(), path.name)


def read_ocel(path: Path, object_type: str, attributes: Iterable[str] | None = None) -> Log:
    """An OCEL 2.0 JSON log flattened on one object type, the case notion: each object of
    that type is a case, its events are the events related to it, and its attributes are
    the object's attributes (the latest value of each)."""
    data = path.read_bytes()
    try:
        doc = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LogError(f"{path.name} is not JSON: {exc}") from exc
    types = {t.get("name") for t in doc.get("objectTypes", [])}
    if object_type not in types:
        raise LogError(
            f"{path.name} has no object type {object_type!r} (types: {', '.join(sorted(map(str, types)))})"
        )
    wanted = set(attributes) if attributes is not None else None
    cases: dict[str, Case] = {}
    for obj in doc.get("objects", []):
        if obj.get("type") != object_type:
            continue
        case = Case(str(obj["id"]))
        latest: dict[str, tuple[str, Any]] = {}
        for a in obj.get("attributes", []):
            name, at = a.get("name"), str(a.get("time", ""))
            if name is None or (wanted is not None and name not in wanted):
                continue
            v = a.get("value")
            v = v if isinstance(v, int | float | bool) else value(None if v is None else str(v))
            if name not in latest or at >= latest[name][0]:
                latest[name] = (at, v)
        case.attrs = {k: v for k, (_, v) in latest.items()}
        cases[case.id] = case
    for ev in doc.get("events", []):
        related = {str(r.get("objectId")) for r in ev.get("relationships", [])}
        when = timestamp(str(ev.get("time")))
        for cid in sorted(related & cases.keys()):
            cases[cid].events.append(Event(str(ev.get("type")), when))
    cases = {k: c for k, c in cases.items() if c.events}
    return Log(_finish(cases), "ocel", hashlib.sha256(data).hexdigest(), path.name)

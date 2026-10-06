"""What the adapter is told about a log: one YAML file per process and log.

process: Purchase requests          # the process the cases belong to
log:
  file: purchase-requests.csv       # relative to this file
  format: csv                       # csv, xes or ocel (default: from the extension)
  columns: {case: case_id, activity: activity, timestamp: timestamp, role: role}
  attributes: [amount, urgent]      # case attributes (CSV: columns; XES, OCEL: names)
  object_type: purchase request     # OCEL: the object type that is the case
case_object: Purchase request       # the business object a case is (a data entity)
system: Coupa                       # the system the steps are done in (a component)
origin: system:coupa                # who the log comes from (default: system:<system>)
labels:                             # the log's activity labels -> step names
  PR_SUBMIT: Submit purchase request
min_count: 1                        # directly-follows pairs seen fewer times are left out
views:                              # compare: each view's collections (ADR 0032); the view
  as written: ["sop:fin-007"]       # as done defaults to the log's and its conformance digest's
  as told: ["interview:nw-2026-09-15-a"]
rank:                               # rank (ADR 0033): each factor's weight, 1 when not given
  weights: {waiting: 2, rework: 1}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wmk_process import logs

AS_DONE = "as done"
# What rank scores each step on (ADR 0033), in the order it prints them.
FACTORS = ("volume", "waiting", "rework", "handoffs", "rule", "system")


class ConfigError(ValueError):
    """A configuration the adapter cannot use."""


@dataclass
class Config:
    process: str
    file: Path
    format: str
    columns: logs.Columns = field(default_factory=logs.Columns)
    attributes: tuple[str, ...] | None = None
    object_type: str | None = None
    case_object: str | None = None
    system: str | None = None
    origin: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    min_count: int = 1
    views: dict[str, tuple[str, ...]] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict.fromkeys(FACTORS, 1.0))

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.process.lower()).strip("-")

    @property
    def collection(self) -> str:
        """Every export of this log is one source: a newer one supersedes the older."""
        return f"event-log:{self.slug}"

    @property
    def conformance_collection(self) -> str:
        return f"conformance:{self.slug}"

    @property
    def origins(self) -> tuple[str, ...]:
        if self.origin:
            return (self.origin,)
        if self.system:
            return (f"system:{self.system.lower()}",)
        return ()

    def compared(self) -> dict[str, tuple[str, ...]]:
        """The views `compare` reads, each with its collections: those configured, and the
        view as done (the log and its conformance digest) unless it is configured too."""
        done = {} if AS_DONE in self.views else {AS_DONE: (self.collection, self.conformance_collection)}
        return {**self.views, **done}

    def step(self, label: str) -> str:
        """The step name an activity label stands for."""
        return self.labels.get(label, label)

    def read(self) -> logs.Log:
        if not self.file.is_file():
            raise ConfigError(f"no log at {self.file}")
        if self.format == "csv":
            return logs.read_csv(self.file, self.columns)
        if self.format == "xes":
            return logs.read_xes(self.file, self.attributes)
        if self.object_type is None:
            raise ConfigError("an OCEL log needs log.object_type: the object type that is the case")
        return logs.read_ocel(self.file, self.object_type, self.attributes)


def load(path: Path | str) -> Config:
    path = Path(path)
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    unknown = set(raw) - {
        "process",
        "log",
        "case_object",
        "system",
        "origin",
        "labels",
        "min_count",
        "views",
        "rank",
    }
    if unknown:
        raise ConfigError(f"{path.name}: unknown keys {', '.join(sorted(unknown))}")
    log = raw.get("log") or {}
    if not raw.get("process") or not log.get("file"):
        raise ConfigError(f"{path.name}: process and log.file are required")
    file = (path.parent / log["file"]).resolve()
    fmt = log.get("format") or {".csv": "csv", ".xes": "xes", ".json": "ocel", ".jsonocel": "ocel"}.get(
        file.suffix.lower(), ""
    )
    if fmt not in ("csv", "xes", "ocel"):
        raise ConfigError(f"{path.name}: log.format must be csv, xes or ocel")
    attributes = tuple(log["attributes"]) if "attributes" in log else None
    cols = dict(log.get("columns") or {})
    bad = set(cols) - {"case", "activity", "timestamp", "role", "lifecycle"}
    if bad:
        raise ConfigError(f"{path.name}: unknown log.columns {', '.join(sorted(bad))}")
    columns = logs.Columns(**cols, attributes=attributes or ())
    min_count = int(raw.get("min_count", 1))
    if min_count < 1:
        raise ConfigError(f"{path.name}: min_count must be at least 1")
    views = raw.get("views") or {}
    if not isinstance(views, dict) or not all(
        isinstance(v, list) and v and all(isinstance(c, str) and c for c in v) for v in views.values()
    ):
        raise ConfigError(f"{path.name}: views must map each view's name to a list of collections")
    rank = raw.get("rank") or {}
    given = rank.get("weights") or {} if isinstance(rank, dict) else None
    if (
        not isinstance(given, dict)
        or set(rank) - {"weights"}
        or set(given) - set(FACTORS)
        or not all(isinstance(w, int | float) and not isinstance(w, bool) and w >= 0 for w in given.values())
    ):
        raise ConfigError(
            f"{path.name}: rank takes weights, a map from {', '.join(FACTORS)} to numbers of 0 or more"
        )
    return Config(
        process=str(raw["process"]),
        file=file,
        format=fmt,
        columns=columns,
        attributes=attributes,
        object_type=log.get("object_type"),
        case_object=raw.get("case_object"),
        system=raw.get("system"),
        origin=raw.get("origin"),
        labels={str(k): str(v) for k, v in (raw.get("labels") or {}).items()},
        min_count=min_count,
        views={str(k): tuple(v) for k, v in views.items()},
        weights={f: float(given.get(f, 1)) for f in FACTORS},
    )

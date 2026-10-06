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
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wmk_process import logs


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
    unknown = set(raw) - {"process", "log", "case_object", "system", "origin", "labels", "min_count"}
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
    )

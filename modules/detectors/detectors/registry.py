"""Detectors from a YAML file (KK_DETECTORS_FILE) and from `python: module:attr` entries.

  detectors:
    - name: unheld-post              # a-z, 0-9, dashes
      owner: kkm_detectors           # the db role the query runs as: it sees what that role is cleared for
      scope: urn:kk:scope:default    # where the events are written (the module's role needs to write it)
      anchored: true                 # false: the query always sees everything
      sql: |                         # key, nodes (text[]), claims (uuid[]), data (jsonb, optional); %(anchors)s
        select ...
    - python: map_gaps.detector:DETECTOR
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import yaml

from .engine import Detector

NAME = re.compile(r"^[a-z][a-z0-9-]{0,60}$")


def load(path: str | Path) -> list[Detector]:
    out = []
    for e in (yaml.safe_load(Path(path).read_text()) or {}).get("detectors", []):
        if "python" in e:
            mod, _, attr = e["python"].partition(":")
            out.append(getattr(importlib.import_module(mod), attr))
            continue
        if not NAME.match(e["name"]):
            raise ValueError(f"detector name {e['name']!r}: lower case letters, digits and dashes")
        if not e.get("sql"):
            raise ValueError(f"detector {e['name']}: needs sql")
        out.append(Detector(e["name"], e["owner"], sql=e["sql"], scope=e.get("scope", "urn:kk:scope:default"),
                            anchored=e.get("anchored", True)))
    names = [d.name for d in out]
    if len(set(names)) != len(names):
        raise ValueError("detector names must be unique")
    return out

"""Eval fixtures from event logs: the digest the adapter ingests, the plan rendered as a
fixture script (evals/player.py) and fixture.yaml. The expected graph is written by hand,
so the eval checks the mapping against a person's reading of the same digest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wmk_adapter import script
from wmk_adapter.plan import Plan
from wmk_process import config, discover


def write(cfg_path: Path, folder: Path, *, name: str) -> Plan:
    """Write (or refresh) a fixture: sources/, fixture.yaml (keeping a description and
    thresholds already there) and script.yaml. expected.yaml is left alone."""
    cfg = config.load(cfg_path)
    plan = discover.build(cfg, cfg.read())
    old = yaml.safe_load((folder / "fixture.yaml").read_text()) if (folder / "fixture.yaml").exists() else {}
    sources: dict[str, Any] = {}
    for source in plan.sources:
        file = f"sources/{source.alias}"
        target = folder / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.content)
        args = source.ingest_args()
        del args["content"]
        sources[source.alias] = {"file": file, **args}
    meta = {
        "name": name,
        "description": old.get("description", f"The event log of {cfg.process}, mapped by wmk-process."),
        "namespaces": ["process"],
        "packs": ["process"],
        "sources": sources,
        "script": "script.yaml",
        "expected": "expected.yaml",
        "thresholds": old.get(
            "thresholds",
            {"entities": {"precision": 1.0, "recall": 1.0}, "edges": {"precision": 1.0, "recall": 1.0}},
        ),
    }
    (folder / "fixture.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=110))
    (folder / "script.yaml").write_text(script.script_text(plan, "wmk-process snapshot"))
    return plan

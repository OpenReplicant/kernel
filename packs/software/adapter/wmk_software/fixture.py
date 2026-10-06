"""Eval fixtures from repositories: a snapshot of the files the adapter maps at one commit,
the git history up to it, and the plan rendered as a fixture script (evals/player.py).
The expected graph is written by hand, so the eval checks the mapping against a person's
reading of the same files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wmk_adapter import script
from wmk_software.mapping import build
from wmk_software.plan import Plan
from wmk_software.repo import HISTORY_FILE, Repository


def load(folder: Path | str) -> tuple[Repository, dict[str, Any]]:
    """The repository a fixture was taken from, rebuilt from its sources, and its fixture.yaml."""
    folder = Path(folder)
    meta = yaml.safe_load((folder / "fixture.yaml").read_text())
    repo = meta["repository"]
    files: dict[str, str] = {}
    history = ""
    for spec in meta["sources"].values():
        path = spec["file"]
        text = (folder / path).read_text()
        if path.endswith(HISTORY_FILE):
            history = text
        else:
            files[spec["metadata"]["path"]] = text
    return (
        Repository(
            url=repo["url"],
            name=repo["name"],
            commit=repo["commit"],
            branch=repo["branch"],
            files=files,
            history=history,
        ),
        meta,
    )


def plan_for(folder: Path | str) -> Plan:
    repo, meta = load(folder)
    return build(repo, self_system=meta["repository"].get("self"))


def script_text(plan: Plan) -> str:
    """The fixture script, one line per claim and per operation."""
    return script.script_text(plan, "wmk-software snapshot")


def write(repo: Repository, folder: Path | str, *, name: str, self_system: str | None) -> Plan:
    """Write (or refresh) a fixture: sources/, fixture.yaml (keeping a description and
    thresholds already there) and script.yaml. expected.yaml is left alone."""
    folder = Path(folder)
    plan = build(repo, self_system=self_system)
    old = yaml.safe_load((folder / "fixture.yaml").read_text()) if (folder / "fixture.yaml").exists() else {}
    sources: dict[str, Any] = {}
    for source in plan.sources:
        file = f"sources/{HISTORY_FILE}" if source.alias == "git-history" else f"sources/{source.alias}"
        target = folder / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.content)
        args = source.ingest_args()
        del args["content"]
        sources[source.alias] = {"file": file, **args}
    meta = {
        "name": name,
        "description": old.get("description", f"The {repo.name} repository at {repo.commit[:7]}."),
        "namespaces": ["software"],
        "packs": ["software"],
        "repository": {
            "url": repo.url,
            "name": repo.name,
            "commit": repo.commit,
            "branch": repo.branch,
            **({"self": self_system} if self_system else {}),
        },
        "sources": sources,
        "script": "script.yaml",
        "expected": "expected.yaml",
        "thresholds": old.get(
            "thresholds",
            {"entities": {"precision": 1.0, "recall": 1.0}, "edges": {"precision": 1.0, "recall": 1.0}},
        ),
    }
    (folder / "fixture.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=110))
    (folder / "script.yaml").write_text(script_text(plan))
    return plan

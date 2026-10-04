"""Skills and packs stay usable: valid Agent Skills frontmatter, working links, profiles that
name existing skills, and every Cypher example runs through query_graph as written."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from gateway import profiles
from kernel.testing import KernelDB

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md")) + sorted((ROOT / "packs").glob("*/SKILL.md"))
DOCS = sorted(p for d in ("skills", "packs") for p in (ROOT / d).rglob("*.md"))
CYPHER = re.compile(r"```cypher\n(.*?)```", re.S)
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    return yaml.safe_load(text.split("---\n")[1])


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: str(p.relative_to(ROOT)))
def test_skill_frontmatter_follows_agent_skills(path: Path) -> None:
    meta = frontmatter(path)
    assert NAME.match(meta["name"]) and len(meta["name"]) <= 64
    assert 0 < len(meta["description"]) <= 1024
    assert "kernel" in meta["metadata"]
    for link in re.findall(r"\]\(([^)#]+)\)", path.read_text()):
        if "://" not in link:
            assert (path.parent / link).exists(), f"{path}: broken link {link}"


def test_profiles_name_existing_skills() -> None:
    names = {p.parent.name for p in SKILLS}
    for profile in (ROOT / "profiles").glob("*.yaml"):
        listed = yaml.safe_load(profile.read_text())["skills"]
        assert set(listed) <= names, f"{profile.name}: unknown skills {set(listed) - names}"
        assert profiles.load(profile).name


def cypher_examples() -> list[tuple[str, str]]:
    return [
        (f"{path.relative_to(ROOT)}#{i}", block)
        for path in DOCS
        for i, block in enumerate(CYPHER.findall(path.read_text()))
    ]


@pytest.mark.anyio
async def test_every_cypher_example_runs(dbname: str) -> None:
    examples = cypher_examples()
    assert len(examples) >= 10
    kdb = KernelDB(dbname)
    try:
        agent = kdb.register()
        refs = kdb.claim(
            agent,
            "A small process",
            [
                {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
                {"op": "create", "ref": "$role", "kind": "role", "namespace": "bpm", "name": "Approver"},
                {"op": "create", "ref": "$p", "kind": "process", "namespace": "bpm", "name": "Invoices"},
                {"op": "create", "ref": "$s", "kind": "activity", "namespace": "bpm", "name": "Approve"},
                {"op": "assert", "edge": "part_of", "from": "$s", "to": "$p"},
                {"op": "assert", "edge": "implements", "from": "$dana", "to": "$role"},
            ],
            basis="observed",
        )["refs"]
    finally:
        kdb.close()

    from kernel.testing import gateway_client

    async with gateway_client(dbname) as client:
        for where, cypher in examples:
            params = {name: refs["$p"] for name in re.findall(r"\$(\w+)", cypher)}
            result = await client.call_tool("query_graph", {"cypher": cypher, "params": params})
            body = json.loads(result.content[0].text)
            assert not result.is_error, f"{where}: {body.get('detail')}"

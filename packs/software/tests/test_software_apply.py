"""The software adapter against a kernel, through the gateway: the fixture maps to the expected
graph, mapping again writes nothing, a changed file supersedes and retracts what it no longer
states while history stays, existing nodes are reused, and the trigram mirror agrees with
Postgres."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.compare import compare, produced_graph
from kernel import admin
from kernel.testing import KernelDB
from wmk_software import fixture
from wmk_software.apply import apply
from wmk_software.mapping import build
from wmk_software.plan import SELF, similarity
from wmk_software.repo import Repository

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "kernel-stack"
URL = "https://github.com/example/toy"


def toy(compose: str, commits: list[str]) -> Repository:
    lines = [f"{c * 40} 2026-10-0{i + 1}T12:00:00Z Change {i + 1} (#{i + 1})" for i, c in enumerate(commits)]
    history = f"Repository: {URL}\nBranch: main\n\nFirst-parent commits, newest first:\n\n"
    history += "\n".join(reversed(lines)) + "\n"
    return Repository(
        url=URL,
        name="example/toy",
        commit=commits[-1] * 40,
        branch="main",
        files={"compose.yaml": compose},
        history=history,
    )


V1 = """\
name: toy
services:
  web:
    image: nginx:1.27
    depends_on: [cache]
  cache:
    image: redis:7
"""
V2 = """\
name: toy
services:
  web:
    image: nginx:1.28
"""


async def test_the_fixture_maps_to_the_expected_graph_and_again_to_nothing(gateway: Any, dbname: str) -> None:
    plan = fixture.plan_for(FIXTURE)
    first = await apply(gateway, plan)
    assert first.failures == []
    assert first.claims == len(plan.facts)
    expected = yaml.safe_load((FIXTURE / "expected.yaml").read_text())
    result = compare(expected, produced_graph(admin.dsn_for(admin.admin_dsn(), dbname)))
    assert (result.entities.precision, result.entities.recall) == (1.0, 1.0), result.entities
    assert (result.edges.precision, result.edges.recall) == (1.0, 1.0), result.edges
    assert result.attribute_errors == []

    again = await apply(gateway, plan)
    assert (again.claims, again.created, again.unchanged, again.failures) == (0, 0, len(plan.sources), [])


async def test_a_changed_file_supersedes_what_it_no_longer_says(gateway: Any, kdb: KernelDB) -> None:
    first = await apply(gateway, build(toy(V1, ["a"])))
    assert first.failures == [] and first.retracted == 0
    second = await apply(gateway, build(toy(V2, ["a", "b"])))
    assert second.failures == []
    # runs web -> nginx:1.27, part_of cache -> toy, runs cache -> redis, needs web -> cache
    assert second.retracted == 4
    runs = dict(kdb.q("SELECT props ->> 'tag', belief_status FROM kernel.edges WHERE kind = 'runs'"))
    assert runs["1.28"] == "accepted" and runs["1.27"] != "accepted" and runs["7"] != "accepted"
    parts = dict(
        kdb.q(
            "SELECT f.name, e.belief_status FROM kernel.edges e JOIN kernel.nodes f ON f.id = e.from_id "
            "WHERE e.edge = 'part_of' AND e.kind IS NULL"
        )
    )
    assert (
        parts == {"toy/web": "accepted", "toy/cache": parts["toy/cache"]} and parts["toy/cache"] != "accepted"
    )
    # History is append-only: the first commit is not retracted by a history that grew.
    changed = kdb.q("SELECT belief_status FROM kernel.edges WHERE edge = 'participates_in'")
    assert [s for (s,) in changed] == ["accepted", "accepted"]
    # The retraction is one observed claim citing the new version of the file.
    claim = kdb.q("SELECT text, basis FROM kernel.claims_view ORDER BY log_offset DESC LIMIT 1")[0]
    assert claim == (
        "The current compose.yaml no longer states 4 facts that an earlier version did.",
        "observed",
    )

    third = await apply(gateway, build(toy(V2, ["a", "b"])))
    assert (third.claims, third.retracted) == (0, 0)


async def test_existing_nodes_are_reused_by_identity(gateway: Any, kdb: KernelDB) -> None:
    head = json.loads((await gateway.call_tool("query_log", {"limit": 1})).content[0].text)["head_offset"]
    result = await gateway.call_tool(
        "write",
        {
            "claim": {"text": "The cache runs Redis.", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": head,
            "ops": [
                {
                    "op": "create",
                    "type": "Entity",
                    "kind": "image",
                    "namespace": "software",
                    "name": "Redis",
                    "identity": {"purl": "pkg:docker/redis"},
                }
            ],
        },
    )
    assert not result.is_error
    report = await apply(gateway, build(toy(V1, ["a"])))
    assert report.failures == [] and report.reused == 1
    assert (
        kdb.one(
            "SELECT count(*) FROM kernel.nodes "
            "WHERE kind = 'image' AND identity ->> 'purl' = 'pkg:docker/redis'"
        )
        == 1
    )


async def test_the_self_agent_comes_from_the_gateway_before_any_write(gateway: Any, kdb: KernelDB) -> None:
    plan = build(toy(V1, ["a"]), self_system="Toy (this instance)")
    # The agent's claim first, before anything is written in this run.
    plan.facts.sort(key=lambda f: not any(op.get("from") == SELF for op in f.ops))
    report = await apply(gateway, plan)
    assert report.failures == []
    agent = kdb.one(
        "SELECT n.name FROM kernel.nodes n JOIN kernel.edges e ON e.from_id = n.id "
        "WHERE e.edge = 'part_of' AND n.type = 'Agent'"
    )
    assert agent == "wmk-eval"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("wmk/otel-collector:4317", "wmk/otel-collector:4318"),
        ("opentelemetry-api", "opentelemetry-sdk"),
        ("psycopg", "psycopg-pool"),
        ("Crème brûlée", "creme brulee"),
        ("ci/lint", "ci/kernel"),
    ],
)
def test_trigram_similarity_mirrors_postgres(kdb: KernelDB, a: str, b: str) -> None:
    pg = kdb.one("SELECT similarity(kernel.normalize_name(%s), kernel.normalize_name(%s))", [a, b])
    assert similarity(a, b) == pytest.approx(float(pg), abs=1e-4)

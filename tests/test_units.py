"""Unit tests for the Cypher helpers, profiles and the replay diff (no database needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.replay import SNAPSHOTS, diff
from gateway import profiles
from gateway.cypher import CypherError, check_read_only, dollar_tag, elements, parse_agtype, return_columns

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("query", "columns"),
    [
        ("MATCH (n) RETURN n", ["n"]),
        ("MATCH (a)-[e]->(b) RETURN a.name AS who, e, count(*) ORDER BY who LIMIT 3", ["who", "e", "count"]),
        ("MATCH (n {name: 'a, RETURN b'}) RETURN DISTINCT n.name, [1, 2] AS xs", ["n_name", "xs"]),
        ("MATCH (n) WITH n, {k: 1, j: 2} AS m RETURN m.k AS k SKIP 1", ["k"]),
        ("MATCH p = (a)-[*1..2]->(b) RETURN p", ["p"]),
    ],
)
def test_return_columns(query: str, columns: list[str]) -> None:
    assert return_columns(query) == columns


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DETACH DELETE n",
        "CREATE (n:Entity) RETURN n",
        "MATCH (n) SET n.x = 1 RETURN n",
        "MERGE (n {id: 'x'}) RETURN n",
        "MATCH (n) REMOVE n.name RETURN n",
        "MATCH (n) RETURN n; DROP TABLE x",
    ],
)
def test_updating_queries_are_refused(query: str) -> None:
    with pytest.raises(CypherError):
        check_read_only(query)


def test_keywords_inside_strings_and_comments_are_fine() -> None:
    check_read_only("MATCH (n {name: 'CREATE SET DELETE'}) // DELETE everything\nRETURN n")
    with pytest.raises(CypherError):
        return_columns("MATCH (n) RETURN *")


def test_dollar_tag_never_occurs_in_the_query() -> None:
    query = "MATCH (n) RETURN n -- $wmk$"
    assert dollar_tag(query) not in query


def test_parse_agtype_paths_and_annotations() -> None:
    text = (
        '[{"id": 1, "label": "Agent", "properties": {"id": "agt_1", "name": "x::vertex"}}::vertex, '
        '{"id": 9, "label": "implements", "end_id": 2, "start_id": 1, "properties": {"id": "edg_1"}}::edge, '
        '{"id": 2, "label": "Entity", "properties": {"id": "ent_1"}}::vertex]::path'
    )
    path = parse_agtype(text)
    assert [e["id"] for e in elements(path)] == ["agt_1", "edg_1", "ent_1"]
    assert path[0] == {"element": "vertex", "label": "Agent", "id": "agt_1", "name": "x::vertex"}
    assert parse_agtype("2.5::numeric") == 2.5 and parse_agtype(None) is None


def test_profiles_load() -> None:
    eval_profile = profiles.load(ROOT / "profiles" / "eval.yaml")
    assert eval_profile.agent.kind == "machine" and eval_profile.agent.identity == {"profile": "eval"}
    assert eval_profile.person is None and not eval_profile.transcript_capture
    interactive = profiles.load(ROOT / "profiles" / "interactive.yaml")
    assert interactive.person is not None and interactive.person.kind == "human"
    assert interactive.transcript_capture


def test_replay_diff_reports_both_sides() -> None:
    empty = {name: [] for name in SNAPSHOTS}
    live = {**empty, "nodes": ['{"id": "a"}', '{"id": "b"}']}
    rebuilt = {**empty, "nodes": ['{"id": "a"}', '{"id": "c"}']}
    lines = diff(live, rebuilt)
    assert 'nodes - live:    {"id": "b"}' in lines and 'nodes + rebuilt: {"id": "c"}' in lines
    assert diff(live, live) == []


def test_compare_scores_entities_and_edges_separately() -> None:
    from evals.compare import compare

    expected = {
        "entities": [
            {"type": "Entity", "kind": "role", "name": "Approver"},
            {"type": "Agent", "kind": "human", "name": "Dana"},
        ],
        "edges": [{"edge": "implements", "from": "Dana", "to": "Approver", "belief_status": "accepted"}],
        "unresolved_claims": 0,
    }
    produced = {
        "entities": [
            {"type": "Entity", "kind": "role", "name": "approver", "status": "active"},
            {"type": "Agent", "kind": "human", "name": "Sam", "status": "active"},
        ],
        "edges": [
            {
                "edge": "implements",
                "kind": None,
                "from": "Dana",
                "to": "Approver",
                "belief_status": "contested",
            },
            {"edge": "part_of", "kind": None, "from": "Approver", "to": "Finance"},
        ],
        "unresolved_claims": 0,
    }
    result = compare(expected, produced)
    assert (result.entities.precision, result.entities.recall) == (0.5, 0.5)
    assert (result.edges.precision, result.edges.recall) == (0.5, 1.0)
    assert result.attribute_accuracy == 0.0 and "belief_status" in result.attribute_errors[0]

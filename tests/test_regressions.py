"""Regression tests for known failure modes from Graphiti's history.

1. Invalidation must stay scoped to the same edge type and endpoints.
2. Non-overlapping validity windows must never collapse into one edge.
3. Bulk writes must pass the same rule checks as single writes.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import KernelDB, Rejected


@pytest.fixture
def world(kdb: KernelDB, agent: str) -> dict[str, str]:
    result = kdb.claim(
        agent,
        "A small process",
        [
            {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
            {
                "op": "create",
                "ref": "$approver",
                "kind": "role",
                "namespace": "bpm",
                "name": "Invoice approver",
            },
            {
                "op": "create",
                "ref": "$reviewer",
                "kind": "role",
                "namespace": "bpm",
                "name": "Contract reviewer",
            },
            {"op": "create", "ref": "$finance", "kind": "org_unit", "namespace": "bpm", "name": "Finance"},
            {
                "op": "create",
                "ref": "$approve",
                "kind": "activity",
                "namespace": "bpm",
                "name": "Approve invoice",
            },
            {
                "op": "assert",
                "ref": "$e1",
                "edge": "implements",
                "from": "$dana",
                "to": "$approver",
                "valid_from": "2025-01-01",
            },
            {
                "op": "assert",
                "ref": "$e2",
                "edge": "implements",
                "from": "$dana",
                "to": "$reviewer",
                "valid_from": "2025-01-01",
            },
            {"op": "assert", "ref": "$e3", "edge": "responsible_for", "from": "$dana", "to": "$approve"},
            {"op": "assert", "ref": "$e4", "edge": "part_of", "from": "$approver", "to": "$finance"},
        ],
        basis="observed",
    )
    return {k.strip("$"): v for k, v in result["refs"].items()}


def snapshot(kdb: KernelDB, ids: list[str]) -> dict[str, Any]:
    return {
        r[0]: r[1:]
        for r in kdb.q(
            "SELECT id, valid_from, valid_to, belief_status, belief_for, belief_against FROM kernel.edges "
            "WHERE id = ANY(%s)",
            [ids],
        )
    }


def test_invalidation_stays_scoped_to_edge_type_and_endpoints(kdb: KernelDB, agent: str, world: dict) -> None:
    others = [world["e2"], world["e3"], world["e4"]]
    before = snapshot(kdb, others)
    doc = kdb.source(agent, "Dana stopped approving invoices in March 2026 and does not approve them now.")
    # Close and then deny Dana's approver edge.
    kdb.claim(
        agent,
        "Dana stopped approving invoices in March 2026",
        [{"op": "assert", "edge_id": world["e1"], "valid_to": "2026-03-01"}],
        source=doc,
    )
    kdb.claim(
        agent,
        "Dana does not approve invoices",
        [{"op": "assert", "edge_id": world["e1"], "polarity": "negative"}],
        source=doc,
    )
    # A new holder of the same role from another source: a conflict at most, never an invalidation.
    kdb.claim(
        agent,
        "Sam approves invoices",
        [
            {"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam"},
            {
                "op": "assert",
                "edge": "implements",
                "from": "$sam",
                "to": world["approver"],
                "valid_from": "2026-03-01",
            },
        ],
        source=kdb.source(agent, "Sam approves invoices since March 2026."),
    )
    assert snapshot(kdb, others) == before
    # Same endpoints, different edge type: untouched too.
    assert kdb.edge(world["e3"])["belief_status"] == "accepted"
    # The denial came from another source than the original assertion: contested, not overwritten.
    assert kdb.edge(world["e1"])["belief_status"] == "contested"


def test_non_overlapping_windows_never_collapse(kdb: KernelDB, agent: str, world: dict) -> None:
    doc = kdb.source(agent, "Dana was reviewer in 2023, and again from 2025.")
    earlier = kdb.claim(
        agent,
        "Dana was contract reviewer in 2023",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": world["dana"],
                "to": world["reviewer"],
                "valid_from": "2023-01-01",
                "valid_to": "2024-01-01",
            }
        ],
        source=doc,
    )["refs"]["$e"]
    assert earlier != world["e2"]
    assert kdb.edge(earlier)["valid_to"].startswith("2024-01-01")
    assert kdb.edge(world["e2"])["valid_to"] is None
    # Touching windows do not overlap either ([2024-01-01, 2025-01-01) then [2025-01-01, ...)).
    between = kdb.claim(
        agent,
        "Dana was reviewer in 2024",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": world["dana"],
                "to": world["reviewer"],
                "valid_from": "2024-01-01",
                "valid_to": "2025-01-01",
            }
        ],
        source=doc,
    )["refs"]["$e"]
    assert len({earlier, between, world["e2"]}) == 3
    # An overlapping window for the same fact is the same edge.
    same = kdb.claim(
        agent,
        "Dana reviews contracts since 2025",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": world["dana"],
                "to": world["reviewer"],
                "valid_from": "2025-01-01",
            }
        ],
        source=doc,
    )["refs"]["$e"]
    assert same == world["e2"]
    assert (
        kdb.one(
            "SELECT count(*) FROM kernel.edges WHERE from_id = %s AND to_id = %s",
            [world["dana"], world["reviewer"]],
        )
        == 3
    )


def bulk_payloads(world: dict, chunk: str, read_at: int) -> list[dict[str, Any]]:
    base = {"source": chunk, "basis": "reported", "modality": "descriptive"}
    return [
        {
            "claim": {**base, "text": "Approve invoice flows to Finance"},
            "read_at_offset": read_at,
            "ops": [{"op": "assert", "edge": "flows_to", "from": world["approve"], "to": world["finance"]}],
        },
        {
            "claim": {**base, "text": "Gizmo is a widget"},
            "read_at_offset": read_at,
            "ops": [{"op": "create", "kind": "widget", "name": "Gizmo"}],
        },
        {
            "claim": {**base, "text": "Dana approves"},
            "read_at_offset": read_at,
            "ops": [{"op": "assert", "edge": "implements", "from": world["dana"], "to": world["approve"]}],
        },
        {
            "claim": {**base, "text": "A new approver from 2026"},
            "read_at_offset": read_at,
            "ops": [
                {"op": "create", "ref": "$x", "type": "Agent", "kind": "human", "name": "Lee"},
                {
                    "op": "assert",
                    "edge": "implements",
                    "from": "$x",
                    "to": world["approver"],
                    "valid_from": "2026-01-01",
                    "valid_to": "2025-01-01",
                },
            ],
        },
        {
            "claim": {**base, "text": "Dana again", "source": None},
            "read_at_offset": read_at,
            "ops": [{"op": "create", "type": "Agent", "kind": "human", "name": "Dana"}],
        },
        {
            "claim": {**base, "text": "Finance is an ok concept"},
            "read_at_offset": read_at,
            "ops": [{"op": "create", "kind": "concept", "name": "Treasury"}],
        },
    ]


def outcome(kdb: KernelDB, payload: dict[str, Any], agent: str) -> tuple[str | None, str | None]:
    payload = {**payload, "claim": {k: v for k, v in payload["claim"].items() if v is not None}}
    try:
        kdb.write(payload, agent)
    except Rejected as rejection:
        return rejection.problem, rejection.rule
    return None, None


def test_bulk_writes_pass_the_same_rule_checks(kdb: KernelDB, agent: str, world: dict, dbname: str) -> None:
    """A bulk load is many payloads through kernel.write: each gets exactly the single-write verdict."""
    chunk = kdb.source(agent, "Bulk import batch.")
    payloads = bulk_payloads(world, chunk, kdb.head())
    singles = []
    for payload in payloads:
        # Each payload alone, rolled back afterwards, as a single write would see it.
        kdb.writer.execute("BEGIN")
        singles.append(outcome(kdb, payload, agent))
        kdb.writer.execute("ROLLBACK")
    bulk = [outcome(kdb, p, agent) for p in bulk_payloads(world, chunk, kdb.head())]
    assert bulk == singles
    assert bulk == [
        ("domain_range", "bpm.flows_between_steps"),
        ("types", "kernel.known_kind"),
        ("domain_range", "core.implements_role"),
        ("time", "core.window_order"),
        ("provenance", "core.reported_needs_source"),
        (None, None),
    ]

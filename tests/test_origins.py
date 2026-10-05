"""Belief v2 (ADR 0021): each origin counts once.

A source names who its content comes from (origins: authors, a speaker, a publisher); by
default its author agent, else the source itself, and the writer for a claim without a
source. A counted assertion spreads its weight evenly over its origins and each origin adds
its largest share to a side, so sources from the same people are not counted as
independent. Supersession stays per source, disagreement stays contested, v2 equals v1
when every source is its own origin, and adding a source never lowers a side.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from kernel.testing import KernelDB, Rejected


@pytest.fixture
def role(kdb: KernelDB, agent: str) -> dict[str, str]:
    result = kdb.claim(
        agent,
        "Dana and the approver role",
        [
            {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
            {"op": "create", "ref": "$role", "kind": "role", "namespace": "bpm", "name": "Invoice approver"},
        ],
        basis="observed",
    )
    return {k.strip("$"): v for k, v in result["refs"].items()}


def says(
    kdb: KernelDB, agent: str, role: dict[str, str], text: str, polarity: str = "positive", **source: Any
) -> str:
    """Ingest `text` as a source and report from it that Dana approves (or not); the edge id."""
    chunk = kdb.source(agent, text, **source)
    result = kdb.claim(
        agent,
        "Dana approves invoices",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": role["dana"],
                "to": role["role"],
                "polarity": polarity,
            }
        ],
        source=chunk,
    )
    return str(result["refs"]["$e"])


def tally(kdb: KernelDB, edge: str) -> tuple[Decimal, Decimal, int, int, int, int, str]:
    row = kdb.q(
        "SELECT belief_for, belief_against, sources_for, sources_against, origins_for, origins_against, "
        "belief_status FROM kernel.edges WHERE id = %s",
        [edge],
    )[0]
    return row  # type: ignore[return-value]


def test_sources_from_the_same_people_count_once(kdb: KernelDB, agent: str, role: dict[str, str]) -> None:
    edge = says(
        kdb, agent, role, "Paper one: Dana approves invoices.", origins=["orcid:0000-0001", "orcid:0000-0002"]
    )
    w = tally(kdb, edge)[0]
    # A second paper by the same two authors adds a source, not evidence.
    says(
        kdb,
        agent,
        role,
        "Paper two: Dana approves invoices.",
        origins=["ORCID:0000-0002 ", "orcid:0000-0001"],
    )
    assert tally(kdb, edge)[:5] == (w, 0, 2, 0, 2)
    # A third sharing one of two authors adds half of its weight.
    says(
        kdb,
        agent,
        role,
        "Paper three: Dana approves invoices.",
        origins=["orcid:0000-0002", "orcid:0000-0003"],
    )
    assert tally(kdb, edge)[0] == w * Decimal("1.5")
    # A source from someone else counts in full.
    says(kdb, agent, role, "Audit memo: Dana approves invoices.", origins=["domain:audit.example"])
    assert tally(kdb, edge)[:5] == (w * Decimal("2.5"), 0, 4, 0, 4)


def test_one_speaker_in_two_conversations_counts_once(
    kdb: KernelDB, agent: str, role: dict[str, str]
) -> None:
    sam = kdb.claim(
        agent,
        "Sam",
        [{"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam"}],
        basis="observed",
    )["refs"]["$sam"]
    edge = says(kdb, agent, role, "Sam: Dana approves.", collection="conv-1", uri="turn:1", author=sam)
    w = tally(kdb, edge)[0]
    says(kdb, agent, role, "Sam, a week later: Dana approves.", collection="conv-2", uri="turn:1", author=sam)
    assert tally(kdb, edge)[:5] == (w, 0, 2, 0, 1)
    origins = kdb.q("SELECT DISTINCT origins FROM kernel.claims WHERE source_key IN ('conv-1', 'conv-2')")
    assert origins == [([f"agent:{sam.lower()}"],)]


def test_disagreement_within_an_origin_is_still_contested(
    kdb: KernelDB, agent: str, role: dict[str, str]
) -> None:
    edge = says(kdb, agent, role, "Report A: Dana approves.", origins=["domain:acme.example"])
    says(kdb, agent, role, "Report B: Dana does not approve.", "negative", origins=["domain:acme.example"])
    belief_for, against, *_, status = tally(kdb, edge)
    assert belief_for == against and status == "contested"


def test_adding_a_source_never_lowers_a_side(kdb: KernelDB, agent: str, role: dict[str, str]) -> None:
    authors = [
        ["orcid:a", "orcid:b", "orcid:c"],
        ["orcid:c"],
        ["orcid:a", "orcid:d"],
        ["orcid:b", "orcid:c", "orcid:d"],
    ]
    edge = None
    seen: list[Decimal] = []
    for i, origins in enumerate(authors):
        edge = says(kdb, agent, role, f"Study {i}: Dana approves invoices.", origins=origins)
        seen.append(tally(kdb, edge)[0])
    assert seen == sorted(seen)
    # The last study's authors already count with larger shares: it adds nothing.
    assert seen[2] == seen[3] and seen[2] > seen[1] > seen[0]


def test_default_origins_and_v1_equivalence(kdb: KernelDB, agent: str, role: dict[str, str]) -> None:
    a = says(kdb, agent, role, "HR: Dana approves invoices.")
    says(kdb, agent, role, "Email: Dana does not approve invoices.", "negative")
    kdb.claim(agent, "I saw Dana approve", [{"op": "assert", "edge_id": a}], basis="observed")
    # A source with no origins and no author is its own origin; a claim with no source is its writer's.
    assert kdb.q(
        "SELECT count(*) FROM kernel.claims "
        "WHERE source_id IS NOT NULL AND origins <> ARRAY['source:' || source_key]"
    ) == [(0,)]
    assert kdb.q("SELECT DISTINCT origins FROM kernel.claims WHERE source_id IS NULL") == [
        ([f"agent:{agent.lower()}"],)
    ]
    # Every source its own origin: v2 is v1, field by field, for every target.
    rows = kdb.q(
        "SELECT a.target_type, a.target_id, to_jsonb(kernel.belief_v1(a.target_type, a.target_id)), "
        "to_jsonb(kernel.belief_v2(a.target_type, a.target_id)) FROM (SELECT DISTINCT target_type, target_id "
        "FROM kernel.assertions) a"
    )
    for _, _, v1, v2 in rows:
        assert {k: Decimal(str(v)) if isinstance(v, float) else v for k, v in v1.items()} == {
            k: Decimal(str(v)) if isinstance(v, float) else v for k, v in v2.items()
        }


def test_ingest_checks_and_normalises_origins(kdb: KernelDB, agent: str) -> None:
    result = kdb.ingest({"content": "A note.", "origins": [" Name:Ana   Lima", "orcid:x", "orcid:X"]}, agent)
    assert result["origins"] == ["name:ana lima", "orcid:x"]
    again = kdb.ingest({"content": "A note.", "origins": ["domain:other.example"]}, agent)
    assert again["skipped"] and again["origins"] == ["name:ana lima", "orcid:x"]
    for bad in (["no-scheme"], ["source:src_1"], [""], [1], "orcid:x", [f"orcid:{i}" for i in range(65)]):
        with pytest.raises(Rejected) as err:
            kdb.ingest({"content": f"Another note {bad!r}.", "origins": bad}, agent)
        assert err.value.problem == "payload" and err.value.detail["field"] == "origins"


def test_entries_without_origins_replay_as_their_own_source(kdb: KernelDB, agent: str) -> None:
    """Entries logged before kernel 0.4 carry no origins: projection gives each source its own."""
    entry = {
        "log_offset": 10_000,
        "recorded_at": "2026-01-01T00:00:00Z",
        "agent_id": agent,
        "agent_trust": "medium",
        "ops": [],
        "claim": {
            "id": "clm_OLD1",
            "text": "old",
            "source_id": "src_OLD",
            "source_key": "conv-old",
            "basis": "observed",
            "modality": "descriptive",
            "polarity": 1,
            "confidence": "high",
            "resolution": "unresolved",
        },
    }
    with psycopg.connect(kdb.dsn) as conn:
        conn.execute("SET session_replication_role = replica")  # no FK checks on the synthetic rows
        conn.execute("SELECT kernel.project_ops(%s)", [Jsonb(entry)])
        no_source = {
            **entry,
            "log_offset": 10_001,
            "claim": {**entry["claim"], "id": "clm_OLD2", "source_id": None, "source_key": f"agent:{agent}"},
        }
        conn.execute("SELECT kernel.project_ops(%s)", [Jsonb(no_source)])
        got = conn.execute(
            "SELECT id, origins FROM kernel.claims WHERE id LIKE 'clm_OLD%%' ORDER BY id"
        ).fetchall()
        conn.rollback()
    assert got == [("clm_OLD1", ["source:conv-old"]), ("clm_OLD2", [f"agent:{agent.lower()}"])]


def test_replay_reproduces_belief_v2(kdb: KernelDB, agent: str, role: dict[str, str], dbname: str) -> None:
    from evals.replay import replay

    says(kdb, agent, role, "Paper one: Dana approves.", origins=["orcid:a", "orcid:b"])
    says(kdb, agent, role, "Paper two: Dana approves.", origins=["orcid:b"])
    says(kdb, agent, role, "Memo: Dana does not approve.", "negative", origins=["domain:x.example"])
    assert kdb.one("SELECT max(belief_version) FROM kernel.log") == 2
    entries, lines = replay(dbname)
    assert entries == kdb.head() and lines == []

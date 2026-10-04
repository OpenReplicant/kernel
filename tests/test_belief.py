"""Belief: a pure function of assertions. Each source counts once, same source newer supersedes,
different sources make a fact contested, no decay, conflicts never resolved by overwriting."""

from __future__ import annotations

import pytest

from tests.conftest import KernelDB, Rejected


@pytest.fixture
def approver(kdb: KernelDB, agent: str) -> dict[str, str]:
    result = kdb.claim(
        agent,
        "People and the approver role",
        [
            {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
            {"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam"},
            {"op": "create", "ref": "$role", "kind": "role", "namespace": "bpm", "name": "Invoice approver"},
        ],
        basis="observed",
    )
    return {k.strip("$"): v for k, v in result["refs"].items()}


def implements(kdb: KernelDB, agent: str, chunk: str, who: str, role: str, **window: str | None) -> str:
    result = kdb.claim(
        agent,
        f"{who} approves",
        [{"op": "assert", "ref": "$e", "edge": "implements", "from": who, "to": role, **window}],
        source=chunk,
    )
    return result["refs"]["$e"]


def test_each_source_counts_once(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    doc = kdb.source(agent, "Dana approves invoices. Dana really approves invoices.")
    edge = implements(kdb, agent, doc, approver["dana"], approver["role"])
    once = kdb.edge(edge)
    implements(kdb, agent, doc, approver["dana"], approver["role"])
    implements(kdb, agent, doc, approver["dana"], approver["role"])
    again = kdb.edge(edge)
    assert again["belief_for"] == once["belief_for"] and again["sources_for"] == 1
    other = kdb.source(agent, "Payroll memo: Dana approves invoices.")
    implements(kdb, agent, other, approver["dana"], approver["role"])
    assert kdb.edge(edge)["sources_for"] == 2


def test_turns_of_one_conversation_count_as_one_source(
    kdb: KernelDB, agent: str, approver: dict[str, str]
) -> None:
    t1 = kdb.source(agent, "Dana approves.", collection="conv-1", uri="turn:1")
    t2 = kdb.source(agent, "Yes, Dana approves.", collection="conv-1", uri="turn:2")
    edge = implements(kdb, agent, t1, approver["dana"], approver["role"])
    implements(kdb, agent, t2, approver["dana"], approver["role"])
    assert kdb.edge(edge)["sources_for"] == 1


def test_same_source_newer_supersedes(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    doc = kdb.source(agent, "Dana approves. Correction: Dana does not approve.")
    edge = implements(kdb, agent, doc, approver["dana"], approver["role"])
    kdb.claim(
        agent,
        "Dana does not approve",
        [{"op": "assert", "edge_id": edge, "polarity": "negative"}],
        source=doc,
    )
    row = kdb.edge(edge)
    assert row["belief_status"] == "rejected" and row["sources_for"] == 0 and row["sources_against"] == 1


def test_different_source_makes_it_contested(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    a = kdb.source(agent, "HR: Dana approves invoices.")
    b = kdb.source(agent, "Email: Dana does not approve invoices.")
    edge = implements(kdb, agent, a, approver["dana"], approver["role"])
    kdb.claim(
        agent, "Dana does not approve", [{"op": "assert", "edge_id": edge, "polarity": "negative"}], source=b
    )
    row = kdb.edge(edge)
    assert row["belief_status"] == "contested"
    # Both sides stay on record: nothing was overwritten.
    assert kdb.one("SELECT count(*) FROM kernel.assertions WHERE target_id = %s", [edge]) == 2


def test_weak_denial_does_not_contest(kdb: KernelDB, approver: dict[str, str]) -> None:
    strong = kdb.register("strong", trust="high")
    weak = kdb.register("weak", trust="low")
    a = kdb.source(strong, "Signed policy: Dana approves invoices.")
    edge = kdb.claim(
        strong,
        "Dana approves",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": approver["dana"],
                "to": approver["role"],
            }
        ],
        source=a,
        basis="observed",
        confidence="high",
    )["refs"]["$e"]
    kdb.claim(
        weak,
        "Maybe Dana does not approve",
        [{"op": "assert", "edge_id": edge, "polarity": "negative"}],
        basis="inferred",
        confidence="low",
    )
    assert kdb.edge(edge)["belief_status"] == "accepted"


def test_no_decay(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    doc = kdb.source(agent, "Dana approves.")
    edge = implements(kdb, agent, doc, approver["dana"], approver["role"])
    before = kdb.one("SELECT to_jsonb(kernel.belief_v1('edge', %s))", [edge])
    # Shifting the clock does not change belief: the function never reads it.
    kdb.admin.execute("SET timezone = 'Pacific/Kiritimati'")
    after = kdb.one("SELECT to_jsonb(kernel.belief_v1('edge', %s))", [edge])
    assert before == after
    source = kdb.one("SELECT prosrc FROM pg_proc WHERE proname = 'belief_v1'")
    assert "now()" not in source and "recorded_at" not in source and "clock_timestamp" not in source


def test_raw_confidence_maps_to_bands(kdb: KernelDB, agent: str) -> None:
    result = kdb.claim(
        agent, "x", [{"op": "create", "kind": "concept", "name": "X"}], basis="observed", confidence=0.93
    )
    assert kdb.one("SELECT confidence FROM kernel.claims WHERE id = %s", [result["claim_id"]]) == "high"


def test_single_valued_same_source_overlap_is_rejected(
    kdb: KernelDB, agent: str, approver: dict[str, str]
) -> None:
    doc = kdb.source(agent, "Dana approved since 2025; Sam took over in March 2026.")
    dana = implements(kdb, agent, doc, approver["dana"], approver["role"], valid_from="2025-01-01")
    with pytest.raises(Rejected) as err:
        implements(kdb, agent, doc, approver["sam"], approver["role"], valid_from="2026-03-01")
    assert err.value.problem == "cardinality" and err.value.rule == "bpm.approver_cardinality"
    assert err.value.detail["conflicting_edges"] == [dana]
    assert approver["role"] in err.value.detail["detail"] and "2026-03-01" in err.value.detail["detail"]
    # Closing the other window in the same payload is accepted: a clean handover.
    result = kdb.claim(
        agent,
        "Sam took over from Dana in March 2026",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": approver["sam"],
                "to": approver["role"],
                "valid_from": "2026-03-01",
            },
            {"op": "assert", "edge_id": dana, "valid_to": "2026-03-01"},
        ],
        source=doc,
    )
    assert result["conflicts"] == []
    assert (
        kdb.edge(dana)["belief_status"] == "accepted"
        and kdb.edge(result["refs"]["$e"])["belief_status"] == "accepted"
    )
    assert kdb.edge(dana)["valid_to"].startswith("2026-03-01")


def test_single_valued_cross_source_conflict_is_contested(
    kdb: KernelDB, agent: str, approver: dict[str, str]
) -> None:
    hr = kdb.source(agent, "HR: Dana is the approver since 2025.")
    interview = kdb.source(agent, "Sam: I took over approvals in March 2026.")
    dana = implements(kdb, agent, hr, approver["dana"], approver["role"], valid_from="2025-01-01")
    result = kdb.claim(
        agent,
        "Sam took over in March 2026",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": approver["sam"],
                "to": approver["role"],
                "valid_from": "2026-03-01",
            }
        ],
        source=interview,
    )
    sam = result["refs"]["$e"]
    assert result["conflicts"] == [
        {"edge_a": min(dana, sam), "edge_b": max(dana, sam), "rule": "bpm.approver_cardinality"}
    ]
    assert kdb.edge(dana)["belief_status"] == "contested" and kdb.edge(sam)["belief_status"] == "contested"
    assert kdb.edge(dana)["contested_with"] == [sam]
    # The write result says why: the edge it asserted is contested by Dana's edge.
    assert [(e["edge_id"], e["belief_status"], e["contested_with"]) for e in result["edges"]] == [
        (sam, "contested", [dana])
    ]
    # HR later closes Dana's window itself: the conflict is no longer active.
    kdb.claim(
        agent,
        "HR: Dana's approval ended in March 2026",
        [{"op": "assert", "edge_id": dana, "valid_to": "2026-03-01"}],
        source=hr,
    )
    assert kdb.edge(dana)["belief_status"] == "accepted" and kdb.edge(sam)["belief_status"] == "accepted"
    assert kdb.edge(dana)["contested_with"] == []


def test_window_disagreement_is_contested(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    a = kdb.source(agent, "HR: Dana approves since 2025.")
    b = kdb.source(agent, "Audit: Dana approved from 2025 until March 2026.")
    edge = implements(kdb, agent, a, approver["dana"], approver["role"], valid_from="2025-01-01")
    kdb.claim(
        agent,
        "Dana approved until March 2026",
        [{"op": "assert", "edge_id": edge, "valid_to": "2026-03-01"}],
        source=b,
    )
    row = kdb.edge(edge)
    assert row["belief_status"] == "contested" and row["window_agreed"] is False
    assert row["valid_to"] is None  # the union of both windows is shown, not the newer one


def test_belief_as_of_record_time(kdb: KernelDB, agent: str, approver: dict[str, str]) -> None:
    a = kdb.source(agent, "HR: Dana approves.")
    b = kdb.source(agent, "Email: Dana does not approve.")
    edge = implements(kdb, agent, a, approver["dana"], approver["role"])
    before = kdb.head()
    kdb.claim(
        agent, "Dana does not approve", [{"op": "assert", "edge_id": edge, "polarity": "negative"}], source=b
    )
    then = kdb.one("SELECT kernel.edge_state_as_of(%s, %s)", [edge, before])
    now = kdb.one("SELECT kernel.edge_state_as_of(%s, %s)", [edge, kdb.head()])
    assert then["belief_status"] == "accepted" and now["belief_status"] == "contested"
    assert kdb.one("SELECT kernel.edge_state_as_of(%s, %s)", [edge, 1])["known"] is False

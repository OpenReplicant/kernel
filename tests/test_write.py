"""kernel.write: operations, ontology rules, rejections, stale reads, offsets, unresolved claims."""

from __future__ import annotations

import pytest

from tests.conftest import KernelDB, Rejected


def role(kdb: KernelDB, agent: str, name: str = "Invoice approver", chunk: str | None = None) -> str:
    result = kdb.claim(
        agent,
        f"{name} is a role",
        [{"op": "create", "ref": "$r", "kind": "role", "namespace": "bpm", "name": name}],
        basis="observed",
    )
    return result["refs"]["$r"]


def person(kdb: KernelDB, agent: str, name: str, email: str | None = None) -> str:
    op = {"op": "create", "ref": "$p", "type": "Agent", "kind": "human", "name": name}
    if email:
        op["identity"] = {"email": email}
    return kdb.claim(agent, f"{name} works here", [op], basis="observed")["refs"]["$p"]


def test_self_registration_creates_the_writing_agent(kdb: KernelDB) -> None:
    agent = kdb.register("eval", trust="high")
    node = kdb.node(agent)
    assert node["type"] == "Agent" and node["kind"] == "machine" and node["trust_level"] == "high"
    assert kdb.one("SELECT agent_id FROM kernel.log WHERE log_offset = 1") == agent


def test_write_without_agent_must_be_self_registration(kdb: KernelDB) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(None, "x", [{"op": "create", "kind": "concept", "name": "X"}], basis="observed", read_at=0)  # type: ignore[arg-type]
    assert err.value.problem == "agent"


def test_unknown_agent_is_rejected(kdb: KernelDB) -> None:
    kdb.register()
    with pytest.raises(Rejected) as err:
        kdb.claim("agt_nobody", "x", [], basis="observed")
    assert err.value.problem == "agent"


def test_claim_ops_and_graph_commit_together(kdb: KernelDB, agent: str) -> None:
    chunk = kdb.source(agent, "Dana approves invoices.")
    result = kdb.claim(
        agent,
        "Dana approves invoices",
        [
            {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
            {"op": "create", "ref": "$role", "kind": "role", "namespace": "bpm", "name": "Invoice approver"},
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": "$dana",
                "to": "$role",
                "valid_from": "2025-01-01",
            },
        ],
        source=chunk,
    )
    assert result["resolution"] == "resolved"
    edge = kdb.edge(result["refs"]["$e"])
    assert edge["from_id"] == result["refs"]["$dana"] and edge["to_id"] == result["refs"]["$role"]
    assert edge["belief_status"] == "accepted"
    assert kdb.one("SELECT count(*) FROM kernel.claims WHERE id = %s", [result["claim_id"]]) == 1
    assert kdb.one("SELECT count(*) FROM kernel.assertions WHERE claim_id = %s", [result["claim_id"]]) == 3
    assert kdb.one("SELECT source_id FROM kernel.claims WHERE id = %s", [result["claim_id"]]) is not None
    # The AGE mirror carries the edge between the two vertices.
    assert (
        kdb.one(
            "SELECT count(*) FROM ag_catalog.cypher('world', $$ MATCH (:Agent)-[e:implements]->(:Entity) "
            "RETURN e $$) AS (e ag_catalog.agtype)"
        )
        == 1
    )


def test_offsets_are_gapless_and_record_time_monotonic(kdb: KernelDB, agent: str) -> None:
    for i in range(3):
        kdb.claim(
            agent,
            f"concept {i}",
            [{"op": "create", "kind": "concept", "name": f"Concept {i}"}],
            basis="observed",
        )
    with pytest.raises(Rejected):
        kdb.claim(agent, "bad", [{"op": "create", "kind": "nonsense", "name": "X"}], basis="observed")
    kdb.claim(
        agent, "concept 9", [{"op": "create", "kind": "concept", "name": "Concept 9"}], basis="observed"
    )
    offsets = [r[0] for r in kdb.q("SELECT log_offset FROM kernel.log ORDER BY log_offset")]
    assert offsets == list(range(1, len(offsets) + 1))
    times = [r[0] for r in kdb.q("SELECT recorded_at FROM kernel.log ORDER BY log_offset")]
    assert times == sorted(times)


def test_unresolved_claim_is_kept_without_ops(kdb: KernelDB, agent: str) -> None:
    chunk = kdb.source(agent, "The approval process feels slow.")
    result = kdb.write(
        {
            "claim": {
                "text": "The approval process feels slow",
                "source": chunk,
                "basis": "reported",
                "modality": "descriptive",
            },
            "read_at_offset": kdb.head(),
            "ops": [],
            "unresolved": {"reason": "no kind for perceived speed"},
        },
        agent,
    )
    assert result["resolution"] == "unresolved"
    row = kdb.q("SELECT resolution, unresolved, text FROM kernel.claims WHERE id = %s", [result["claim_id"]])[
        0
    ]
    assert row[0] == "unresolved" and row[1] == {"reason": "no kind for perceived speed"}
    assert row[2] == "The approval process feels slow"


def test_unknown_kind_names_nearest_kinds(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "x", [{"op": "create", "kind": "activty", "name": "Approve"}], basis="observed")
    assert err.value.problem == "types" and err.value.rule == "kernel.known_kind"
    assert err.value.detail["nearest"][0] == "activity"


def test_namespace_types_rule(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [{"op": "create", "kind": "component", "namespace": "bpm", "name": "ERP"}],
            basis="observed",
        )
    assert err.value.problem == "types" and err.value.rule == "bpm.allowed_kinds"
    assert "activity" in err.value.detail["nearest"]


def test_domain_range_names_rule_and_nearest_edges(kdb: KernelDB, agent: str) -> None:
    approver = role(kdb, agent)
    act = kdb.claim(
        agent,
        "Approve invoice is an activity",
        [{"op": "create", "ref": "$a", "kind": "activity", "namespace": "bpm", "name": "Approve invoice"}],
        basis="observed",
    )["refs"]["$a"]
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent, "x", [{"op": "assert", "edge": "flows_to", "from": approver, "to": act}], basis="observed"
        )
    assert err.value.problem == "domain_range" and err.value.rule == "bpm.flows_between_steps"
    assert "responsible_for" in err.value.detail["nearest"]
    # implements must point at a role (core rule)
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent, "x", [{"op": "assert", "edge": "implements", "from": act, "to": act}], basis="observed"
        )
    assert err.value.rule == "core.implements_role"


def test_edge_specialisation_is_stored_on_the_kernel_edge(kdb: KernelDB, agent: str) -> None:
    result = kdb.claim(
        agent,
        "Check invoice reads the purchase order",
        [
            {"op": "create", "ref": "$a", "kind": "activity", "namespace": "bpm", "name": "Check invoice"},
            {"op": "create", "ref": "$d", "kind": "data", "name": "Purchase order"},
            {"op": "assert", "ref": "$e", "edge": "reads_from", "from": "$a", "to": "$d"},
        ],
        basis="observed",
    )
    edge = kdb.edge(result["refs"]["$e"])
    assert edge["edge"] == "depends_on" and edge["kind"] == "reads_from"
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [
                {
                    "op": "assert",
                    "edge": "reads_from",
                    "from": result["refs"]["$d"],
                    "to": result["refs"]["$a"],
                }
            ],
            basis="observed",
        )
    assert err.value.rule == "bpm.reads_from_data"


def test_time_rules(kdb: KernelDB, agent: str) -> None:
    dana = person(kdb, agent, "Dana")
    approver = role(kdb, agent)
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [
                {
                    "op": "assert",
                    "edge": "implements",
                    "from": dana,
                    "to": approver,
                    "valid_from": "2026-03-01",
                    "valid_to": "2026-01-01",
                }
            ],
            basis="observed",
        )
    assert err.value.problem == "time" and err.value.rule == "core.window_order"
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [
                {
                    "op": "create",
                    "type": "Event",
                    "kind": "meeting",
                    "name": "Review",
                    "start": "2026-03-02",
                    "end": "2026-03-01",
                }
            ],
            basis="observed",
        )
    assert err.value.rule == "core.event_order"
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [{"op": "assert", "edge": "implements", "from": dana, "to": approver, "valid_from": "March"}],
            basis="observed",
        )
    assert err.value.problem == "payload"


def test_identity_rules(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        person(kdb, agent, "Sam", email="not-an-email")
    assert err.value.problem == "identity" and err.value.rule == "core.human_email"
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [{"op": "create", "type": "Agent", "kind": "human", "name": "Sam", "identity": {"badge": "42"}}],
            basis="observed",
        )
    assert err.value.rule == "kernel.declared_identity" and err.value.detail["nearest"] == ["email"]


def test_provenance_rules(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "Dana approves", [], basis="reported")
    assert err.value.problem == "provenance" and err.value.rule == "core.reported_needs_source"
    dana = person(kdb, agent, "Dana")
    plan = kdb.claim(
        agent,
        "Budget plan",
        [{"op": "create", "ref": "$p", "kind": "data", "name": "Budget plan"}],
        basis="observed",
    )["refs"]["$p"]
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "Dana probably approved the plan",
            [{"op": "assert", "edge": "approved_by", "from": plan, "to": dana}],
            basis="inferred",
        )
    assert err.value.problem == "provenance" and err.value.rule == "bpm.approval_needs_report"


def test_unknown_reference_lists_candidates(kdb: KernelDB, agent: str) -> None:
    person(kdb, agent, "Dana Ruiz")
    approver = role(kdb, agent)
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [{"op": "assert", "edge": "implements", "from": "Dana Ruiz", "to": approver}],
            basis="observed",
        )
    assert err.value.problem == "reference"
    assert err.value.detail["candidates"][0]["name"] == "Dana Ruiz"


def test_payload_shape_errors(kdb: KernelDB, agent: str) -> None:
    cases = [
        {"claim": {"text": "x", "basis": "maybe", "modality": "descriptive"}, "read_at_offset": 1, "ops": []},
        {
            "claim": {"text": "", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": 1,
            "ops": [],
        },
        {"claim": {"text": "x", "basis": "observed", "modality": "descriptive"}, "ops": []},
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": 1,
            "ops": [{"op": "delete", "node": "x"}],
        },
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": 1,
            "ops": [{"op": "create", "kind": "concept", "name": "X", "colour": "red"}],
        },
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": 999,
            "ops": [],
        },
        {
            "claim": {"text": "x", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": 1,
            "ops": [],
            "sql": "DROP TABLE kernel.log",
        },
    ]
    for payload in cases:
        with pytest.raises(Rejected) as err:
            kdb.write(payload, agent)
        assert err.value.problem == "payload", payload


def test_stale_read_is_rejected(kdb: KernelDB, agent: str) -> None:
    other = kdb.claim(
        agent,
        "other agent",
        [
            {
                "op": "create",
                "type": "Agent",
                "kind": "machine",
                "name": "other",
                "identity": {"profile": "other"},
            }
        ],
        basis="observed",
    )["ops"][0]["id"]
    approver = role(kdb, agent)
    dana = person(kdb, agent, "Dana")
    read_at = kdb.head()
    # Another agent changes the role after our read.
    kdb.claim(
        other,
        "Approver is part of finance",
        [
            {"op": "create", "ref": "$f", "kind": "concept", "name": "Finance"},
            {"op": "assert", "edge": "part_of", "from": approver, "to": "$f"},
        ],
        basis="observed",
    )
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "Dana is approver",
            [{"op": "assert", "edge": "implements", "from": dana, "to": approver}],
            basis="observed",
            read_at=read_at,
        )
    assert err.value.problem == "stale"
    assert {c["node_id"] for c in err.value.detail["changed"]} == {approver}
    # The agent's own writes after its read do not make it stale.
    kdb.claim(
        agent,
        "Dana is approver",
        [{"op": "assert", "edge": "implements", "from": dana, "to": approver}],
        basis="observed",
        read_at=kdb.head(),
    )
    kdb.claim(
        agent,
        "Dana is approver from 2025",
        [
            {
                "op": "assert",
                "edge": "implements",
                "from": dana,
                "to": approver,
                "valid_from": "2025-01-01",
                "valid_to": "2025-02-01",
            }
        ],
        basis="observed",
        read_at=kdb.head() - 1,
    )


def test_transition_and_contested_status(kdb: KernelDB, agent: str) -> None:
    review = kdb.claim(
        agent,
        "A review is planned",
        [
            {
                "op": "create",
                "ref": "$e",
                "type": "Event",
                "kind": "meeting",
                "name": "Quarterly review",
                "status": "planned",
                "start": "2026-10-01",
            }
        ],
        basis="observed",
    )["refs"]["$e"]
    assert kdb.node(review)["status"] == "planned"
    assert kdb.node(review)["props"]["start"] == "2026-10-01T00:00:00Z"
    kdb.claim(
        agent,
        "The review happened",
        [{"op": "transition", "node": review, "status": "completed"}],
        basis="observed",
    )
    assert kdb.node(review)["status"] == "completed"
    chunk = kdb.source(agent, "The quarterly review was cancelled.")
    kdb.claim(
        agent,
        "The review was cancelled",
        [{"op": "transition", "node": review, "status": "cancelled"}],
        source=chunk,
    )
    node = kdb.node(review)
    assert node["status"] == "contested" and node["status_options"] == ["cancelled", "completed"]
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "x", [{"op": "transition", "node": review, "status": "exploded"}], basis="observed")
    assert err.value.rule == "kernel.known_status"


def test_promote_creates_claim_node_about_targets(kdb: KernelDB, agent: str) -> None:
    act = kdb.claim(
        agent,
        "Approve invoice",
        [{"op": "create", "ref": "$a", "kind": "activity", "namespace": "bpm", "name": "Approve invoice"}],
        basis="observed",
    )["refs"]["$a"]
    chunk = kdb.source(agent, "Every invoice must be approved before payment.")
    result = kdb.claim(
        agent,
        "Every invoice must be approved before payment",
        [{"op": "promote", "ref": "$c", "about": [act]}],
        source=chunk,
        modality="normative",
    )
    node = kdb.node(result["claim_id"])
    assert node["type"] == "Claim" and node["kind"] == "normative" and node["status"] == "open"
    assert node["belief_status"] == "accepted" and node["props"]["text"].startswith("Every invoice")
    assert (
        kdb.one(
            "SELECT count(*) FROM kernel.edges WHERE edge = 'about' AND from_id = %s AND to_id = %s",
            [result["claim_id"], act],
        )
        == 1
    )
    # Another source denies the claim: contested.
    chunk2 = kdb.source(agent, "Small invoices are paid without approval.")
    kdb.claim(
        agent,
        "Small invoices are paid without approval",
        [{"op": "assert", "claim_id": result["claim_id"], "polarity": "negative"}],
        source=chunk2,
    )
    assert kdb.node(result["claim_id"])["belief_status"] == "contested"


def test_link_and_unlink_same_as_never_merge(kdb: KernelDB, agent: str) -> None:
    a = person(kdb, agent, "Sam Ortiz")
    b = kdb.claim(
        agent,
        "S. Ortiz",
        [
            {
                "op": "create",
                "ref": "$p",
                "type": "Agent",
                "kind": "human",
                "name": "S. Ortiz",
                "distinct_from": [a],
            }
        ],
        basis="observed",
    )["refs"]["$p"]
    linked = kdb.claim(
        agent, "Sam Ortiz and S. Ortiz are one person", [{"op": "link", "from": b, "to": a}], basis="inferred"
    )
    edge_id = linked["ops"][0]["edge_id"]
    assert kdb.edge(edge_id)["edge"] == "same_as" and kdb.edge(edge_id)["belief_status"] == "accepted"
    kdb.claim(agent, "They are different people", [{"op": "unlink", "from": a, "to": b}], basis="inferred")
    assert kdb.edge(edge_id)["belief_status"] == "rejected"
    assert kdb.one("SELECT count(*) FROM kernel.nodes WHERE id IN (%s, %s)", [a, b]) == 2


def test_redact_masks_graph_and_log_views(kdb: KernelDB, agent: str) -> None:
    sam = person(kdb, agent, "Sam Ortiz", email="sam@acme.test")
    chunk = kdb.source(agent, "Sam's home address is 1 Main St.")
    secret = kdb.claim(agent, "Sam lives at 1 Main St", [], source=chunk)
    kdb.claim(
        agent,
        "Erase Sam's personal data",
        [
            {"op": "redact", "node": sam, "fields": ["name", "identity"]},
            {"op": "redact", "claim": secret["claim_id"]},
        ],
        basis="observed",
    )
    node = kdb.node(sam)
    assert (
        node["name"] == "[redacted]"
        and node["identity"] == {}
        and set(node["redacted"]) == {"identity", "name"}
    )
    assert kdb.one("SELECT text FROM kernel.claims_view WHERE id = %s", [secret["claim_id"]]) == "[redacted]"
    ops = kdb.one("SELECT ops FROM kernel.log_entries WHERE ops @> %s::jsonb", [f'[{{"id": "{sam}"}}]'])
    assert ops[0]["name"] == "[redacted]" and ops[0]["identity"] == {}
    vertex = kdb.one(
        'SELECT properties::text FROM world."Agent" WHERE properties::text LIKE %s', [f"%{sam}%"]
    )
    assert "Sam Ortiz" not in vertex

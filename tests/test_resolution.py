"""The resolution cascade on create: identity keys -> normalized exact -> pg_trgm -> pgvector."""

from __future__ import annotations

import pytest

from tests.conftest import KernelDB, Rejected


def create(kdb: KernelDB, agent: str, op: dict, **kw) -> dict:
    return kdb.claim(
        agent, f"{op['name']} exists", [{"op": "create", "ref": "$n", **op}], basis="observed", **kw
    )


def test_identity_key_match_is_certain_and_cannot_be_waived(kdb: KernelDB, agent: str) -> None:
    sam = create(
        kdb,
        agent,
        {"type": "Agent", "kind": "human", "name": "Sam Ortiz", "identity": {"email": "sam@acme.test"}},
    )["refs"]["$n"]
    with pytest.raises(Rejected) as err:
        create(
            kdb,
            agent,
            {
                "type": "Agent",
                "kind": "human",
                "name": "Samuel O.",
                "identity": {"email": "SAM@acme.test"},
                "distinct_from": [sam],
            },
        )
    assert err.value.problem == "duplicate"
    assert err.value.detail["candidates"][0] == {
        **err.value.detail["candidates"][0],
        "node_id": sam,
        "band": "certain",
        "stage": "identity",
    }


def test_normalized_exact_match_rejects_until_distinct_from(kdb: KernelDB, agent: str) -> None:
    first = create(kdb, agent, {"kind": "activity", "namespace": "bpm", "name": "Approve invoice"})["refs"][
        "$n"
    ]
    with pytest.raises(Rejected) as err:
        create(kdb, agent, {"kind": "activity", "namespace": "bpm", "name": "approve  INVOICE!"})
    assert err.value.detail["candidates"][0]["stage"] == "normalized"
    second = create(
        kdb,
        agent,
        {"kind": "activity", "namespace": "bpm", "name": "approve  INVOICE!", "distinct_from": [first]},
    )["refs"]["$n"]
    assert second != first


def test_aliases_resolve(kdb: KernelDB, agent: str) -> None:
    create(kdb, agent, {"kind": "component", "name": "SAP S/4HANA", "aliases": ["the ERP"]})
    rows = kdb.q("SELECT name, stage, band FROM kernel.resolve_candidates('The ERP', 'Entity')")
    assert rows[0] == ("SAP S/4HANA", "normalized", "high")


def test_trigram_band(kdb: KernelDB, agent: str) -> None:
    create(kdb, agent, {"kind": "activity", "namespace": "bpm", "name": "Approve supplier invoice"})
    result = create(kdb, agent, {"kind": "activity", "namespace": "bpm", "name": "Approve invoice"})
    # Ambiguous candidates do not block; they are returned for the model to decide.
    assert result["ambiguous"][0]["candidates"][0]["name"] == "Approve supplier invoice"
    assert result["ambiguous"][0]["candidates"][0]["band"] == "ambiguous"
    with pytest.raises(Rejected) as err:
        create(kdb, agent, {"kind": "activity", "namespace": "bpm", "name": "Approve supplier invoices"})
    assert err.value.detail["candidates"][0]["stage"] == "trigram"
    assert err.value.detail["candidates"][0]["band"] == "high"


def test_vector_stage(kdb: KernelDB, agent: str) -> None:
    create(kdb, agent, {"kind": "concept", "name": "Accounts payable", "embedding": [1.0, 0.0, 0.0]})
    rows = kdb.q(
        "SELECT name, stage, band FROM kernel.resolve_candidates('Creditors ledger', 'Entity', NULL, '{}', "
        "'[0.99, 0.05, 0.0]'::vector)"
    )
    assert rows == [("Accounts payable", "vector", "high")]
    with pytest.raises(Rejected) as err:
        create(kdb, agent, {"kind": "concept", "name": "Creditors ledger", "embedding": [0.99, 0.05, 0.0]})
    assert err.value.detail["candidates"][0]["stage"] == "vector"


def test_kind_scopes_name_matching(kdb: KernelDB, agent: str) -> None:
    create(kdb, agent, {"kind": "role", "namespace": "bpm", "name": "Approver"})
    create(kdb, agent, {"kind": "concept", "name": "Approver"})  # same name, different kind: not a duplicate
    assert kdb.one("SELECT count(*) FROM kernel.nodes WHERE name = 'Approver'") == 2


def test_duplicate_creates_in_one_payload(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(
            agent,
            "x",
            [
                {"op": "create", "kind": "concept", "name": "Billing"},
                {"op": "create", "kind": "concept", "name": "billing"},
            ],
            basis="observed",
        )
    assert err.value.problem == "duplicate"

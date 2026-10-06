"""The self boundary in approvals (ADR 0019, 0029): a person who is part of a system cannot
approve changes to it or its parts; someone outside it can. The boundary is belief, so a
retracted membership no longer binds."""

from __future__ import annotations

import pytest

from kernel.testing import KernelDB, Rejected


def test_approvers_are_outside_the_system_they_change(kdb: KernelDB, agent: str) -> None:
    refs = kdb.claim(
        agent,
        "The kernel instance runs the gateway; Dana operates it.",
        [
            {
                "op": "create",
                "ref": "$sys",
                "kind": "system",
                "namespace": "software",
                "name": "Kernel (this instance)",
            },
            {"op": "create", "ref": "$gw", "kind": "service", "namespace": "software", "name": "wmk/gateway"},
            {
                "op": "create",
                "ref": "$dana",
                "type": "Agent",
                "kind": "human",
                "name": "Dana Ruiz",
                "identity": {"email": "dana@example.test"},
            },
            {"op": "assert", "edge": "part_of", "from": "$gw", "to": "$sys"},
            {"op": "assert", "edge": "part_of", "from": "$dana", "to": "$sys"},
        ],
        basis="inferred",
    )["refs"]
    proposal = kdb.claim(
        agent,
        "Raise the gateway's request timeout.",
        [{"op": "promote", "ref": "$p", "about": [refs["$gw"]], "props": {"change": {"setting": "timeout"}}}],
        basis="inferred",
        modality="proposed",
    )["refs"]["$p"]

    with pytest.raises(Rejected) as err:
        kdb.decide("dana@example.test", {"action": "approve", "proposal": proposal})
    assert (err.value.problem, err.value.rule) == ("governance", "core.approver_outside_system")
    assert err.value.detail["systems"] == [refs["$sys"]]

    # Once the system no longer counts Dana as a part, she may decide.
    membership = kdb.one(
        "SELECT id FROM kernel.edges WHERE edge = 'part_of' AND from_id = %s AND to_id = %s",
        [refs["$dana"], refs["$sys"]],
    )
    kdb.claim(
        agent,
        "Dana no longer operates the instance.",
        [{"op": "assert", "edge_id": membership, "polarity": "negative"}],
        basis="inferred",
    )
    kdb.decide("dana@example.test", {"action": "approve", "proposal": proposal})
    assert kdb.one("SELECT status FROM kernel.nodes WHERE id = %s", [proposal]) == "approved"

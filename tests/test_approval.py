"""The approval channel (ADR 0029, building ADR 0019): proposals are data, decisions come
only from a signed-in person through kernel.decide, nobody decides on their own proposal,
instruments need two people and stand alone, and the log replays exactly."""

from __future__ import annotations

import json
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from kernel.testing import KernelDB, Rejected, login_dsn

DANA = "dana@example.test"
OMAR = "omar@example.test"


def thing(kdb: KernelDB, agent: str, name: str) -> str:
    return kdb.claim(
        agent,
        f"{name} exists",
        [{"op": "create", "ref": "$t", "kind": "data", "name": name}],
        basis="observed",
    )["refs"]["$t"]


def propose(kdb: KernelDB, agent: str, about: list[str], text: str = "Add a canary stage.") -> str:
    result = kdb.claim(
        agent,
        text,
        [
            {
                "op": "promote",
                "ref": "$p",
                "about": about,
                "props": {"change": {"kind": "setting", "value": "canary"}},
            }
        ],
        basis="inferred",
        modality="proposed",
    )
    return str(result["refs"]["$p"])


def status(kdb: KernelDB, node: str) -> str:
    return str(kdb.one("SELECT status FROM kernel.nodes WHERE id = %s", [node]))


def rejected(call: Any) -> tuple[str, str | None]:
    with pytest.raises(Rejected) as err:
        call()
    return err.value.problem, err.value.rule


def test_proposals_and_hypotheses_are_data(kdb: KernelDB, agent: str) -> None:
    plan = thing(kdb, agent, "Budget plan")
    other = thing(kdb, agent, "Cost centre")
    proposal = propose(kdb, agent, [plan])
    assert status(kdb, proposal) == "open"
    # What a proposal states about other nodes would count as support: refused.
    for modality in ("proposed", "hypothetical"):
        assert rejected(
            lambda m=modality: kdb.claim(
                agent,
                "The plan should depend on the cost centre.",
                [{"op": "assert", "edge": "depends_on", "from": plan, "to": other}],
                basis="inferred",
                modality=m,
            )
        ) == ("governance", "core.proposals_are_data")
    assert rejected(
        lambda: kdb.claim(
            agent,
            "Maybe a new plan.",
            [{"op": "create", "kind": "data", "name": "New plan"}],
            basis="inferred",
            modality="hypothetical",
        )
    ) == ("governance", "core.proposals_are_data")
    # Relating the hypothesis itself to evidence is fine.
    finding = kdb.claim(agent, "Budgets overran.", [{"op": "promote", "ref": "$f"}], basis="inferred")[
        "refs"
    ]["$f"]
    kdb.claim(
        agent,
        "Overruns come from late approvals.",
        [
            {"op": "promote", "ref": "$h", "about": [plan]},
            {"op": "assert", "edge": "supports", "from": finding, "to": "$h"},
        ],
        basis="inferred",
        modality="hypothetical",
    )


def test_agents_never_decide(kdb: KernelDB, agent: str, dbname: str) -> None:
    proposal = propose(kdb, agent, [thing(kdb, agent, "Budget plan")])
    dana = kdb.claim(
        agent,
        "Dana is the controller.",
        [
            {
                "op": "create",
                "ref": "$d",
                "type": "Agent",
                "kind": "human",
                "name": "Dana Ruiz",
                "identity": {"email": DANA},
            }
        ],
        basis="observed",
    )["refs"]["$d"]
    # The machine approving itself, or in Dana's name, or moving the status: all refused.
    for ops in (
        [{"op": "assert", "edge": "approved_by", "from": proposal, "to": agent}],
        [{"op": "assert", "edge": "approved_by", "from": proposal, "to": dana}],
        [{"op": "assert", "edge": "rejected_by", "from": proposal, "to": dana}],
        [{"op": "transition", "node": proposal, "status": "approved"}],
    ):
        assert rejected(lambda o=ops: kdb.claim(agent, "Approved.", o, basis="observed")) == (
            "governance",
            "core.decided_by_person",
        )
    # Writing as Dana's agent with her token's claims set, but not as the approver role.
    with kdb.writer.transaction():
        kdb.writer.execute("SELECT set_config('request.jwt.claims', %s, true)", [json.dumps({"email": DANA})])
        assert rejected(
            lambda: kdb.claim(
                dana,
                "Approved.",
                [{"op": "assert", "edge": "approved_by", "from": proposal, "to": dana}],
                basis="observed",
            )
        ) == ("governance", "core.decided_by_person")
    # A machine protecting instruments is a decision too.
    assert rejected(
        lambda: kdb.claim(
            agent,
            "Protected.",
            [{"op": "promote", "about": [proposal], "props": {"instruments": True}}],
            basis="observed",
            modality="normative",
        )
    ) == ("governance", "core.decided_by_person")
    assert status(kdb, proposal) == "open"
    # The gateway's logins cannot decide, and the approver cannot write anything else.
    for login in ("wmk_writer", "wmk_reader"):
        with psycopg.connect(login_dsn(dbname, login), autocommit=True) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT kernel.decide('{}'::jsonb)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SET ROLE kernel_approver")
    with kdb.approver.transaction():
        kdb.approver.execute("SET LOCAL ROLE kernel_approver")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            kdb.approver.execute("SELECT kernel.write(%s, %s)", [Jsonb({}), agent])
    from gateway.tools import TOOL_NAMES

    assert not any("decide" in name or "approve" in name for name in TOOL_NAMES)


def test_a_person_decides_as_themselves(kdb: KernelDB, agent: str) -> None:
    plan = thing(kdb, agent, "Budget plan")
    proposal = propose(kdb, agent, [plan])
    result = kdb.decide(
        DANA, {"action": "approve", "proposal": proposal, "reason": "Low risk."}, name="Dana Ruiz"
    )
    dana = result["agent_id"]
    assert (result["decision"], result["approvals"], result["needed"]) == ("approve", 1, 1)
    assert status(kdb, proposal) == "approved"
    # Dana's agent registered itself from the token: human, keyed by her email, sealed.
    person = kdb.node(dana)
    assert (person["kind"], person["name"], person["identity"]) == ("human", "Dana Ruiz", {"email": DANA})
    edge = kdb.q(
        "SELECT e.belief_status FROM kernel.edges e "
        "WHERE e.edge = 'approved_by' AND e.from_id = %s AND e.to_id = %s",
        [proposal, dana],
    )
    assert edge == [("accepted",)]
    text, basis = kdb.q("SELECT text, basis FROM kernel.claims_view WHERE id = %s", [result["claim_id"]])[0]
    assert (text, basis) == ("Approved the proposal: Add a canary stage. Reason: Low risk.", "observed")
    # Deciding again on a decided proposal is refused; another proposal is rejected.
    assert rejected(lambda: kdb.decide(DANA, {"action": "reject", "proposal": proposal}))[0] == "payload"
    second = propose(kdb, agent, [plan], "Drop the review step.")
    kdb.decide(OMAR, {"action": "reject", "proposal": second})
    assert status(kdb, second) == "rejected"
    # A token without an email names nobody.
    assert rejected(lambda: kdb.decide("", {"action": "approve", "proposal": second})) == (
        "governance",
        "core.decided_by_person",
    )


def test_nobody_decides_on_their_own_proposal(kdb: KernelDB, agent: str) -> None:
    plan = thing(kdb, agent, "Budget plan")
    dana = kdb.decide(DANA, {"action": "reject", "proposal": propose(kdb, agent, [plan])})["agent_id"]
    # Dana proposes something herself (through any writer) and tries to approve it.
    mine = propose(kdb, dana, [plan], "Raise the threshold to 20,000 euros.")
    assert rejected(lambda: kdb.decide(DANA, {"action": "approve", "proposal": mine})) == (
        "governance",
        "core.no_self_approval",
    )
    kdb.decide(OMAR, {"action": "approve", "proposal": mine})
    assert status(kdb, mine) == "approved"


def test_only_the_proposer_withdraws(kdb: KernelDB, agent: str) -> None:
    proposal = propose(kdb, agent, [thing(kdb, agent, "Budget plan")])
    other = kdb.register("other-agent")
    assert rejected(
        lambda: kdb.claim(
            other,
            "Withdrawn.",
            [{"op": "transition", "node": proposal, "status": "withdrawn"}],
            basis="observed",
        )
    ) == ("governance", "core.withdraw_own")
    kdb.claim(
        agent, "Withdrawn.", [{"op": "transition", "node": proposal, "status": "withdrawn"}], basis="observed"
    )
    assert status(kdb, proposal) == "withdrawn"


def test_instruments_need_two_people_and_stand_alone(kdb: KernelDB, agent: str, dbname: str) -> None:
    from evals.replay import replay

    rules = thing(kdb, agent, "Approval rules")
    plan = thing(kdb, agent, "Budget plan")
    protected = kdb.decide(DANA, {"action": "protect", "about": [rules]})
    # The protecting claim's node is the claim itself.
    guard = protected["claim_id"]
    assert kdb.one("SELECT kernel.is_instrument(%s)", [rules]) is True
    assert kdb.one("SELECT kernel.is_instrument(%s)", [guard]) is True
    assert kdb.one("SELECT kernel.is_instrument(%s)", [plan]) is False

    change = propose(kdb, agent, [rules], "Let one person approve rule changes.")
    first = kdb.decide(DANA, {"action": "approve", "proposal": change})
    assert (first["approvals"], first["needed"], status(kdb, change)) == (1, 2, "open")
    assert rejected(lambda: kdb.decide(DANA, {"action": "approve", "proposal": change}))[0] == "payload"
    kdb.decide(OMAR, {"action": "approve", "proposal": change})
    assert status(kdb, change) == "approved"

    # A change to an instrument and to what it judges, in one proposal: split it.
    mixed = propose(kdb, agent, [rules, plan], "Loosen the rules and the plan together.")
    assert rejected(lambda: kdb.decide(DANA, {"action": "approve", "proposal": mixed})) == (
        "governance",
        "core.instruments_alone",
    )
    # The protecting claim is an instrument itself: unprotecting needs two people too.
    unprotect = propose(kdb, agent, [guard], "Stop protecting the approval rules.")
    assert kdb.decide(OMAR, {"action": "approve", "proposal": unprotect})["needed"] == 2
    assert status(kdb, unprotect) == "open"

    entries, lines = replay(dbname)
    assert entries == kdb.head() and lines == []

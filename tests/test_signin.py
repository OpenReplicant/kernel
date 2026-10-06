"""Signing in to decide (ADR 0030): PostgREST's login holds nothing until it switches to the
reader or the approver, the explorer reads proposals from one view, the page learns who a
token names, and tokens are minted with the standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import psycopg
import pytest

from kernel.testing import KernelDB, login_dsn
from kernel.token import ROLE, TokenError, mint
from tests.test_approval import DANA, propose, thing

SECRET = "s" * 32


def as_role(kdb: KernelDB, role: str, claims: dict[str, Any] | None, query: str) -> Any:
    """One query as PostgREST runs it: the role switched for the transaction, with the
    token's claims, if any, in request.jwt.claims."""
    with kdb.approver.transaction():
        kdb.approver.execute(f"SET LOCAL ROLE {role}")
        if claims is not None:
            kdb.approver.execute("SELECT set_config('request.jwt.claims', %s, true)", [json.dumps(claims)])
        row = kdb.approver.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_the_api_login_holds_nothing_until_it_switches(dbname: str) -> None:
    with psycopg.connect(login_dsn(dbname, "wmk_api"), autocommit=True) as conn:
        inherit, groups = conn.execute(
            "SELECT r.rolinherit, array_agg(g.rolname ORDER BY g.rolname) FROM pg_roles r "
            "JOIN pg_auth_members m ON m.member = r.oid JOIN pg_roles g ON g.oid = m.roleid "
            "WHERE r.rolname = 'wmk_api' GROUP BY r.rolinherit"
        ).fetchone() or (None, None)
        assert (inherit, groups) == (False, ["kernel_approver", "kernel_reader"])
        # NOINHERIT: as itself it can neither read nor decide.
        for query in ("SELECT count(*) FROM kernel.nodes", "SELECT kernel.decide('{}'::jsonb)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)
        # As the reader it reads and cannot decide; it can never become the writer.
        conn.execute("SET ROLE kernel_reader")
        assert conn.execute("SELECT count(*) FROM kernel.proposals_view").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT kernel.decide('{}'::jsonb)")
        conn.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SET ROLE kernel_writer")


def test_the_proposals_view(kdb: KernelDB, agent: str) -> None:
    rules = thing(kdb, agent, "Approval rules")
    plan = thing(kdb, agent, "Budget plan")
    kdb.decide(DANA, {"action": "protect", "about": [rules]})
    change = propose(kdb, agent, [rules], "Let one person approve rule changes.")
    finding = kdb.claim(
        agent,
        "Rule changes wait a week for a second approver.",
        [{"op": "promote", "ref": "$f"}, {"op": "assert", "edge": "supports", "from": "$f", "to": change}],
        basis="inferred",
    )["refs"]["$f"]
    dana = kdb.decide(DANA, {"action": "approve", "proposal": change})["agent_id"]
    other = propose(kdb, agent, [plan], "Drop the review step.")
    kdb.decide("omar@example.test", {"action": "reject", "proposal": other})

    rows = {
        r[0]: r[1:]
        for r in kdb.q(
            "SELECT id, text, status, change, proposer, about, approvers, rejecters, needed, instrument, "
            "evidence FROM kernel.proposals_view"
        )
    }
    assert set(rows) == {change, other}
    text, status, what, proposer, about, approvers, rejecters, needed, instrument, evidence = rows[change]
    assert (text, status, what, proposer) == (
        "Let one person approve rule changes.",
        "open",
        {"kind": "setting", "value": "canary"},
        agent,
    )
    assert (about, approvers, rejecters, needed, instrument) == ([rules], [dana], [], 2, True)
    assert [(e["edge"], e["from"], e["to"]) for e in evidence] == [("supports", finding, change)]
    _, status, _, _, about, approvers, rejecters, needed, instrument, _ = rows[other]
    assert (status, about, approvers, len(rejecters), needed, instrument) == (
        "rejected",
        [plan],
        [],
        1,
        1,
        False,
    )


def test_signed_in_names_the_person_the_token_names(kdb: KernelDB, agent: str) -> None:
    query = "SELECT kernel.signed_in()"
    # The explorer without a token, as the reader.
    assert as_role(kdb, "kernel_reader", None, query) == {"approver": False}
    # A token, before the person's first decision: no agent yet, the token's name.
    token = {"email": "Dana@Example.test", "name": "Dana Ruiz"}
    assert as_role(kdb, "kernel_approver", token, query) == {
        "email": DANA,
        "name": "Dana Ruiz",
        "approver": True,
    }
    proposal = propose(kdb, agent, [thing(kdb, agent, "Plan")])
    dana = kdb.decide(DANA, {"action": "reject", "proposal": proposal}, name="Dana Ruiz")
    # Once registered, the agent's name, whatever a later token says.
    assert as_role(kdb, "kernel_approver", {"email": DANA, "name": "D."}, query) == {
        "email": DANA,
        "agent_id": dana["agent_id"],
        "name": "Dana Ruiz",
        "approver": True,
    }
    # Claims that are not JSON name nobody.
    with kdb.approver.transaction():
        kdb.approver.execute("SET LOCAL ROLE kernel_approver")
        kdb.approver.execute("SELECT set_config('request.jwt.claims', 'not json', true)")
        assert kdb.approver.execute(query).fetchone() == ({"approver": True},)


def test_the_email_is_the_identity(kdb: KernelDB, agent: str) -> None:
    """A person of the same name already in the graph without an email may be someone else:
    the signed-in person registers as a distinct agent instead of being refused as a
    duplicate."""
    seeded = kdb.claim(
        agent,
        "Dana Ruiz approves purchases.",
        [{"op": "create", "ref": "$d", "type": "Agent", "kind": "human", "name": "Dana Ruiz"}],
        basis="inferred",
    )["refs"]["$d"]
    proposal = propose(kdb, agent, [thing(kdb, agent, "Budget plan")])
    signed = kdb.decide(DANA, {"action": "approve", "proposal": proposal}, name="Dana Ruiz")["agent_id"]
    assert signed != seeded
    assert kdb.node(signed)["identity"] == {"email": DANA}
    ops = kdb.one(
        "SELECT ops FROM kernel.log_entries WHERE agent_id = %s ORDER BY log_offset LIMIT 1", [signed]
    )
    assert ops[0]["distinct_from"] == [seeded]


def _decode(part: str) -> Any:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_tokens_are_signed_for_one_person() -> None:
    token = mint(SECRET, " Dana@Example.test ", name="Dana Ruiz", days=2, now=1_000_000)
    header, payload, signature = token.split(".")
    assert _decode(header) == {"alg": "HS256", "typ": "JWT"}
    assert _decode(payload) == {
        "role": ROLE,
        "email": DANA,
        "name": "Dana Ruiz",
        "iat": 1_000_000,
        "exp": 1_000_000 + 2 * 86400,
    }
    expected = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    assert "name" not in _decode(mint(SECRET, DANA).split(".")[1])
    for secret, email in ((SECRET[:31], DANA), ("", DANA), (SECRET, "dana"), (SECRET, "dana@example")):
        with pytest.raises(TokenError):
            mint(secret, email)

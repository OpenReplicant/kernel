"""Erasing personal data by destroying keys (ADR 0022): what is sealed, what erasure reaches
and what it leaves, who may erase, and that replay reproduces the erased graph."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from kernel.testing import KernelDB, Rejected, login_dsn

NAME = "Rosalind Okafor"
EMAIL = "rosalind@acme.test"
SAID = "I approve every invoice over 10,000 euros since March, and Omar signs when I am away."
TITLE = "Interview with Rosalind Okafor"
WORDS = (NAME, "rosalind", EMAIL, "10,000 euros", "Interview with")
REQUEST = {"requested_by": "ticket-17", "approved_by": "dpo-2"}


def stored(kdb: KernelDB, *tables: str) -> str:
    """Every row of the given tables as text; all kernel and graph tables when none are named."""
    if not tables:
        tables = tuple(
            f'"{s}"."{t}"'
            for s, t in kdb.q(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('kernel', 'world') AND table_type = 'BASE TABLE'"
            )
        )
    return "\n".join(str(row[0]) for table in tables for row in kdb.q(f"SELECT t::text FROM {table} t"))


def found(text: str) -> list[str]:
    return [w for w in WORDS if w.lower() in text.lower()]


@pytest.fixture
def interview(kdb: KernelDB, agent: str) -> dict[str, Any]:
    """A person, an interview with them (sealed by default: they are its author), a fact and a
    promoted claim from it."""
    person = kdb.claim(
        agent,
        f"{NAME} joined the finance team.",
        [
            {
                "op": "create",
                "ref": "$p",
                "type": "Agent",
                "kind": "human",
                "name": NAME,
                "aliases": ["Ros"],
                "identity": {"email": EMAIL},
                "embedding": [0.3, 0.1, 0.2],
            },
            {"op": "create", "ref": "$role", "kind": "role", "namespace": "bpm", "name": "Invoice approver"},
        ],
        basis="observed",
    )["refs"]
    ingested = kdb.ingest(
        {
            "content": SAID,
            "title": TITLE,
            "author": person["$p"],
            "collection": "session-1",
            "uri": "turn:1",
            "metadata": {"speaker": NAME},
        },
        agent,
    )
    chunk = ingested["chunks"][0]["id"]
    fact = kdb.claim(
        agent,
        f"{NAME} approves invoices over 10,000 euros.",
        [
            {
                "op": "assert",
                "ref": "$e",
                "edge": "implements",
                "from": person["$p"],
                "to": person["$role"],
                "valid_from": "2026-03-01",
            }
        ],
        source=chunk,
        quote="I approve every invoice over 10,000 euros since March",
    )
    rule = kdb.claim(
        agent,
        "Invoices over 10,000 euros need Rosalind's approval.",
        [{"op": "promote", "about": [person["$role"]]}],
        source=chunk,
        quote="every invoice over 10,000 euros",
        modality="normative",
    )
    return {
        "person": person["$p"],
        "role": person["$role"],
        "source": ingested["source_id"],
        "chunk": chunk,
        "ingested": ingested,
        "edge": fact["refs"]["$e"],
        "fact": fact["claim_id"],
        "rule": rule["claim_id"],
    }


def test_personal_data_is_logged_sealed_and_read_in_the_clear(
    kdb: KernelDB, interview: dict[str, Any]
) -> None:
    # The log, the claims, the sources and their chunks hold none of it in the clear.
    assert found(stored(kdb, "kernel.log", "kernel.claims", "kernel.sources", "kernel.chunks")) == []
    assert kdb.one("SELECT subjects FROM kernel.sources WHERE id = %s", [interview["source"]]) == [
        interview["person"]
    ]
    assert kdb.one("SELECT subjects FROM kernel.data_keys WHERE id = %s", [interview["source"]]) == [
        interview["person"]
    ]
    # Readers, the projection and the write results see it.
    ingested = interview["ingested"]
    assert ingested["subjects"] == [interview["person"]] and ingested["chunks"][0]["text"] == SAID
    person = kdb.node(interview["person"])
    assert (person["name"], person["aliases"], person["identity"]) == (NAME, ["Ros"], {"email": EMAIL})
    assert person["sealed_key"] == interview["person"]
    assert (
        kdb.one("SELECT embedding::text FROM kernel.nodes WHERE id = %s", [interview["person"]])
        == "[0.3,0.1,0.2]"
    )
    view = kdb.q("SELECT text, quote, erased FROM kernel.claims_view WHERE id = %s", [interview["fact"]])[0]
    assert view == (
        f"{NAME} approves invoices over 10,000 euros.",
        "I approve every invoice over 10,000 euros since March",
        False,
    )
    assert kdb.q(
        "SELECT title, content, metadata, sealed FROM kernel.sources_view WHERE id = %s",
        [interview["source"]],
    )[0] == (TITLE, SAID, {"speaker": NAME}, True)
    assert kdb.one("SELECT text FROM kernel.chunks_view WHERE id = %s", [interview["chunk"]]) == SAID
    assert (
        kdb.node(interview["rule"])["props"]["text"] == "Invoices over 10,000 euros need Rosalind's approval."
    )
    entry = kdb.one(
        "SELECT ops -> 0 FROM kernel.log_entries "
        "WHERE claim_id = (SELECT claim_id FROM kernel.nodes WHERE id = %s)",
        [interview["person"]],
    )
    assert (entry["name"], entry["identity"], "sealed" in entry) == (NAME, {"email": EMAIL}, False)


def test_resolution_still_finds_a_sealed_person(kdb: KernelDB, agent: str, interview: dict[str, Any]) -> None:
    for op in (
        {"op": "create", "type": "Agent", "kind": "human", "name": "rosalind okafor"},
        {"op": "create", "type": "Agent", "kind": "human", "name": "R. Okafor", "identity": {"email": EMAIL}},
    ):
        with pytest.raises(Rejected) as err:
            kdb.claim(agent, "Someone joined.", [op], basis="observed")
        assert err.value.problem == "duplicate"
        assert err.value.detail["candidates"][0]["node_id"] == interview["person"]


def test_erasure_destroys_every_readable_copy(kdb: KernelDB, interview: dict[str, Any]) -> None:
    before = kdb.edge(interview["edge"])
    result = kdb.erase({"subject": interview["person"], **REQUEST})
    assert result["keys"] == sorted([interview["person"], interview["source"]])
    assert result["sources"] == [interview["source"]]
    assert result["nodes"] == sorted([interview["person"], interview["rule"]])
    assert result["claims"] == 3  # the person's creation, the fact and the rule
    # Nothing readable is left anywhere in the kernel or the graph.
    assert found(stored(kdb)) == []
    assert kdb.one("SELECT count(*) FROM kernel.data_keys") == 0
    # Readers see that it was erased.
    assert kdb.q("SELECT text, quote, erased FROM kernel.claims_view WHERE id = %s", [interview["fact"]])[
        0
    ] == (
        "[erased]",
        None,
        True,
    )
    assert kdb.q(
        "SELECT title, content, metadata, erased FROM kernel.sources_view WHERE id = %s",
        [interview["source"]],
    )[0] == ("[erased]", "[erased]", {}, True)
    assert kdb.one("SELECT text FROM kernel.chunks_view WHERE id = %s", [interview["chunk"]]) == "[erased]"
    person = kdb.node(interview["person"])
    assert (person["name"], person["aliases"], person["identity"], person["props"]) == (
        "[erased]",
        [],
        {},
        {},
    )
    assert kdb.one("SELECT embedding FROM kernel.nodes WHERE id = %s", [interview["person"]]) is None
    assert kdb.node(interview["rule"])["props"]["text"] == "[erased]"
    # The structure stays: someone held the approver role from March, as believed before.
    after = kdb.edge(interview["edge"])
    for field in ("from_id", "to_id", "valid_from", "belief_status", "belief_for", "sources_for"):
        assert after[field] == before[field], field
    assert kdb.node(interview["role"])["name"] == "Invoice approver"
    ledger = kdb.q("SELECT subject, keys, requested_by, approved_by FROM kernel.erasures")
    assert ledger == [(interview["person"], result["keys"], "ticket-17", "dpo-2")]


def test_replay_reproduces_the_erased_graph(
    kdb: KernelDB, agent: str, interview: dict[str, Any], dbname: str
) -> None:
    from evals.replay import replay

    # A second person whose name was redacted before erasure, and a redaction after it.
    dana = kdb.claim(
        agent,
        "Dana joined.",
        [{"op": "create", "ref": "$d", "type": "Agent", "kind": "human", "name": "Dana Ruiz"}],
        basis="observed",
    )["refs"]["$d"]
    kdb.claim(agent, "redact", [{"op": "redact", "node": dana, "fields": ["name"]}], basis="observed")
    kdb.erase({"subject": dana, **REQUEST})
    kdb.erase({"subject": interview["person"], **REQUEST})
    kdb.claim(agent, "redact", [{"op": "redact", "claim": interview["rule"]}], basis="observed")
    assert kdb.node(dana)["name"] == "[redacted]"
    assert kdb.node(interview["rule"])["name"] == "[redacted]"
    entries, lines = replay(dbname)
    assert entries == kdb.head()
    assert lines == []


def test_an_erased_source_is_neither_cited_nor_stored_again(
    kdb: KernelDB, agent: str, interview: dict[str, Any]
) -> None:
    kdb.erase({"subject": interview["person"], **REQUEST})
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "Still approves.", [], source=interview["chunk"], basis="inferred")
    assert err.value.rule == "kernel.source_erased"
    with pytest.raises(Rejected) as err:
        kdb.ingest({"content": SAID, "collection": "session-1", "uri": "turn:1"}, agent)
    assert err.value.rule == "kernel.source_erased"


def test_the_scope_shows_what_keys_reach_and_what_they_leave(
    kdb: KernelDB, agent: str, interview: dict[str, Any]
) -> None:
    # A public org chart names her too: it is not sealed, so erasure cannot reach it.
    chart = kdb.source(agent, "Finance team: Rosalind Okafor approves invoices.", subjects=[])
    plain = kdb.claim(
        agent,
        "The org chart lists her as an approver.",
        [{"op": "assert", "edge": "implements", "from": interview["person"], "to": interview["role"]}],
        source=chart,
    )
    scope = kdb.erasure_scope(interview["person"])
    assert scope["name"] == NAME
    assert scope["keys"] == sorted([interview["person"], interview["source"]])
    assert [s["source_id"] for s in scope["sources"]] == [interview["source"]]
    assert scope["sources"][0]["title"] == TITLE
    assert scope["claims"] == 3
    assert scope["nodes"] == sorted([interview["person"], interview["rule"]])
    # The role was created by the claim that created her: erasure leaves it as it is.
    assert [n["node_id"] for n in scope["derived_nodes"]] == [interview["role"]]
    assert [c["claim_id"] for c in scope["not_covered"]] == [plain["claim_id"]]
    kdb.erase({"subject": interview["person"], **REQUEST})
    assert kdb.erasure_scope(interview["person"])["erasures"][0]["keys"] == scope["keys"]
    assert kdb.one("SELECT text FROM kernel.claims_view WHERE id = %s", [plain["claim_id"]]) == (
        "The org chart lists her as an approver."
    )


def test_erasing_one_person_leaves_another(kdb: KernelDB, agent: str) -> None:
    people = kdb.claim(
        agent,
        "Two people.",
        [
            {"op": "create", "ref": "$a", "type": "Agent", "kind": "human", "name": "Ana Lima"},
            {"op": "create", "ref": "$b", "type": "Agent", "kind": "human", "name": "Ben Cho"},
        ],
        basis="observed",
    )["refs"]
    a = kdb.ingest({"content": "Ana: I run payroll.", "author": people["$a"]}, agent)
    b = kdb.ingest({"content": "Ben: I run the warehouse.", "author": people["$b"]}, agent)
    both = kdb.ingest(
        {"content": "Ana and Ben share the late shift.", "subjects": [people["$a"], people["$b"]]}, agent
    )
    kdb.erase({"subject": people["$a"], **REQUEST})
    assert kdb.node(people["$a"])["name"] == "[erased]"
    assert kdb.node(people["$b"])["name"] == "Ben Cho"
    texts = dict(kdb.q("SELECT source_id, text FROM kernel.chunks_view"))
    assert texts[a["source_id"]] == "[erased]"
    assert texts[b["source_id"]] == "Ben: I run the warehouse."
    # A source about both goes with either of them: over-erasure is deliberate.
    assert texts[both["source_id"]] == "[erased]"
    # The claim creating both was sealed under the first person's key.
    assert kdb.one("SELECT count(*) FROM kernel.claims_view WHERE text = '[erased]'") == 1


def test_who_is_a_subject(kdb: KernelDB, agent: str) -> None:
    person = kdb.register("Pat Lee", kind="human")
    machine = kdb.register("crawler")
    cases = {
        "by its human author": ({"author": person}, [person]),
        "by a machine author": ({"author": machine}, []),
        "with no author": ({}, []),
        "declared none": ({"author": person, "subjects": []}, []),
        "declared": ({"subjects": [person, machine]}, sorted([person, machine])),
    }
    for i, (extra, subjects) in enumerate(cases.values()):
        result = kdb.ingest({"content": f"Note {i} about the rota.", **extra}, agent)
        assert result["subjects"] == subjects, extra
        sealed = kdb.one(
            "SELECT content LIKE 'wmk:sealed:%%' FROM kernel.sources WHERE id = %s", [result["source_id"]]
        )
        assert sealed is bool(subjects), extra
    with pytest.raises(Rejected) as err:
        kdb.ingest({"content": "Unknown person.", "subjects": ["agt_NOBODY"]}, agent)
    assert err.value.problem == "reference"
    # A person registering themselves is sealed under their own key, claim text included.
    assert kdb.one(
        "SELECT text_key FROM kernel.claims WHERE agent_id = %s ORDER BY log_offset LIMIT 1", [person]
    ) == (person)


def test_erasure_requests_are_checked(kdb: KernelDB, agent: str, interview: dict[str, Any]) -> None:
    plain = kdb.ingest({"content": "A public memo.", "subjects": []}, agent)["source_id"]
    for request, problem in (
        ({"subject": interview["person"], "requested_by": "ticket-17"}, "payload"),
        ({"subject": interview["person"], **REQUEST, "sources": [plain]}, "payload"),
        ({"subject": "agt_NOBODY", **REQUEST}, "reference"),
        ({"subject": agent, **REQUEST}, "payload"),  # a machine agent has no keys
        ({"subject": interview["person"], **REQUEST, "note": "x"}, "payload"),
    ):
        with pytest.raises(Rejected) as err:
            kdb.erase(request)
        assert err.value.problem == problem, request
    kdb.erase({"subject": interview["person"], **REQUEST})
    with pytest.raises(Rejected):
        kdb.erase({"subject": interview["person"], **REQUEST})


def test_only_the_eraser_role_erases(kdb: KernelDB, dbname: str, interview: dict[str, Any]) -> None:
    for login in ("wmk_writer", "wmk_reader"):
        with psycopg.connect(login_dsn(dbname, login), autocommit=True) as conn:
            for statement in (
                "SELECT kernel.erase('{}'::jsonb)",
                "SELECT kernel.erasure_scope('x')",
                "SELECT * FROM kernel.data_keys",
                "DELETE FROM kernel.data_keys",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(statement)
    executable = {
        r[0]
        for r in kdb.q(
            "SELECT p.proname FROM pg_proc p WHERE p.pronamespace = 'kernel'::regnamespace "
            "AND has_function_privilege('kernel_eraser', p.oid, 'EXECUTE')"
        )
    }
    assert executable == {"erase", "erasure_scope"}
    from gateway.tools import TOOL_NAMES

    assert not any("erase" in name for name in TOOL_NAMES)


def test_the_erasure_ledger_is_append_only(kdb: KernelDB, interview: dict[str, Any]) -> None:
    kdb.erase({"subject": interview["person"], **REQUEST})
    for statement in (
        "UPDATE kernel.erasures SET reason = 'x'",
        "DELETE FROM kernel.erasures",
        "TRUNCATE kernel.erasures",
    ):
        with pytest.raises(psycopg.Error) as err:
            kdb.admin.execute(statement)
        assert err.value.sqlstate == "WMK02", statement


@pytest.mark.anyio
async def test_through_the_gateway(gateway: Any, kdb: KernelDB) -> None:
    import json

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await gateway.call_tool(tool, args)
        assert not result.is_error, result.content[0].text
        return json.loads(result.content[0].text)

    head = (await call("query_log", {"limit": 1}))["head_offset"]
    written = await call(
        "write",
        {
            "claim": {"text": f"{NAME} joined.", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": head,
            "ops": [{"op": "create", "ref": "$p", "type": "Agent", "kind": "human", "name": NAME}],
        },
    )
    person = written["refs"]["$p"]
    # The write result shows what was written; the log holds it sealed.
    assert written["ops"][0]["name"] == NAME and "sealed" not in written["ops"][0]
    turn = await call(
        "ingest_source", {"content": SAID, "author": person, "collection": "s-9", "uri": "turn:1"}
    )
    plain = await call(
        "ingest_source", {"content": "A note about the rota.", "author": person, "subjects": []}
    )
    assert (turn["subjects"], plain["subjects"]) == ([person], [])
    assert turn["chunks"][0]["text"] == SAID
    entries = (await call("query_log", {"node_id": person}))["entries"]
    assert entries[0]["claim"]["text"] == f"{NAME} joined." and entries[0]["ops"][0]["name"] == NAME
    assert found(stored(kdb, "kernel.log", "kernel.claims", "kernel.sources", "kernel.chunks")) == []

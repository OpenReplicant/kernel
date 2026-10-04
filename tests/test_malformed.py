"""Malformed input is always a problem document (SQLSTATE WMK01), never a raw database error
and never silently accepted."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import KernelDB, Rejected


@pytest.fixture
def ids(kdb: KernelDB, agent: str) -> dict[str, str]:
    result = kdb.claim(
        agent,
        "two concepts",
        [
            {"op": "create", "ref": "$a", "kind": "concept", "name": "Alpha"},
            {"op": "create", "ref": "$b", "kind": "concept", "name": "Beta"},
            {"op": "assert", "ref": "$e", "edge": "depends_on", "from": "$a", "to": "$b"},
        ],
        basis="observed",
    )
    return {k.strip("$"): v for k, v in result["refs"].items()}


def payload(ops: Any, read: Any = 1, claim: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "claim": {"text": "x", "basis": "observed", "modality": "descriptive", **(claim or {})},
        "read_at_offset": read,
        "ops": ops,
        **extra,
    }


def malformed_writes(a: str, b: str, e: str) -> dict[str, dict[str, Any]]:
    return {
        "fractional read_at_offset": payload([], read=1.5),
        "huge read_at_offset": payload([], read=1e30),
        "string read_at_offset": payload([], read="1"),
        "claim text not a string": payload([], claim={"text": 5}),
        "claim not an object": {"claim": "x", "read_at_offset": 1, "ops": []},
        "ops not a list": payload("nope"),
        "op not an object": payload(["create"]),
        "self not a boolean": payload(
            [{"op": "create", "type": "Agent", "kind": "machine", "name": "z", "self": "yes"}]
        ),
        "op polarity": payload([{"op": "assert", "edge_id": e, "polarity": "maybe"}]),
        "claim polarity": payload([], claim={"polarity": 1}),
        "confidence word": payload([], claim={"confidence": "very"}),
        "confidence out of range": payload([], claim={"confidence": 7}),
        "missing kind": payload([{"op": "create", "name": "Q"}]),
        "name not a string": payload([{"op": "create", "kind": "concept", "name": 5}]),
        "aliases not a list": payload([{"op": "create", "kind": "concept", "name": "Q", "aliases": "x"}]),
        "aliases not strings": payload(
            [{"op": "create", "kind": "concept", "name": "Q", "aliases": [{"a": 1}]}]
        ),
        "identity not an object": payload(
            [{"op": "create", "type": "Agent", "kind": "human", "name": "Q", "identity": [1]}]
        ),
        "identity value not a string": payload(
            [{"op": "create", "type": "Agent", "kind": "human", "name": "Q", "identity": {"email": {"x": 1}}}]
        ),
        "props not an object": payload([{"op": "create", "kind": "concept", "name": "Q", "props": [1]}]),
        "embedding not numbers": payload(
            [{"op": "create", "kind": "concept", "name": "Q", "embedding": "abc"}]
        ),
        "relative date": payload(
            [{"op": "create", "type": "Event", "kind": "meeting", "name": "Q", "start": "yesterday"}]
        ),
        "special date": payload(
            [{"op": "assert", "edge": "depends_on", "from": a, "to": b, "valid_from": "now"}]
        ),
        "date as number": payload(
            [{"op": "assert", "edge": "depends_on", "from": a, "to": b, "valid_from": 2026}]
        ),
        "date as object": payload(
            [{"op": "assert", "edge": "depends_on", "from": a, "to": b, "valid_from": {"y": 1}}]
        ),
        "from not an id": payload([{"op": "assert", "edge": "depends_on", "from": 5, "to": b}]),
        "missing edge": payload([{"op": "assert", "from": a, "to": b}]),
        "unknown edge id": payload([{"op": "assert", "edge_id": "edg_x"}]),
        "claim_id of an entity": payload([{"op": "assert", "claim_id": a}]),
        "link a node to itself": payload([{"op": "link", "from": a, "to": a}]),
        "unlink without link": payload([{"op": "unlink", "from": a, "to": b}]),
        "promote twice": payload([{"op": "promote"}, {"op": "promote"}]),
        "promote about not a list": payload([{"op": "promote", "about": "x"}]),
        "unknown about_edges": payload([{"op": "promote", "about_edges": ["edg_nope"]}]),
        "transition without status": payload([{"op": "transition", "node": a}]),
        "redact fields not a list": payload([{"op": "redact", "node": a, "fields": "name"}]),
        "redact unknown field": payload([{"op": "redact", "node": a, "fields": ["password"]}]),
        "redact unknown claim": payload([{"op": "redact", "claim": "clm_x"}]),
        "ref without $": payload([{"op": "create", "ref": "a", "kind": "concept", "name": "Q"}]),
        "ref reused": payload(
            [
                {"op": "create", "ref": "$q", "kind": "concept", "name": "Q"},
                {"op": "create", "ref": "$q", "kind": "concept", "name": "R"},
            ]
        ),
        "unknown ref": payload([{"op": "assert", "edge": "depends_on", "from": "$zz", "to": b}]),
        "unknown source": payload([], claim={"source": "chk_x"}),
        "unresolved with ops": payload(
            [{"op": "create", "kind": "concept", "name": "Q"}], unresolved={"r": 1}
        ),
        "distinct_from not a list": payload(
            [{"op": "create", "kind": "concept", "name": "Alpha", "distinct_from": a}]
        ),
        "unknown namespace": payload([{"op": "create", "kind": "concept", "namespace": "nope", "name": "Q"}]),
        "start on an entity": payload(
            [{"op": "create", "kind": "concept", "name": "Q", "start": "2020-01-01"}]
        ),
        "edge props not an object": payload(
            [{"op": "assert", "edge": "depends_on", "from": a, "to": b, "props": "x"}]
        ),
    }


def test_malformed_writes_are_rejected(kdb: KernelDB, agent: str, ids: dict[str, str]) -> None:
    unhandled = []
    for label, body in malformed_writes(ids["a"], ids["b"], ids["e"]).items():
        kdb.writer.execute("BEGIN")
        try:
            kdb.write(body, agent)
            unhandled.append(f"{label}: accepted")
        except Rejected:
            pass
        except psycopg.Error as exc:
            unhandled.append(f"{label}: raw {exc.sqlstate}")
        finally:
            kdb.writer.execute("ROLLBACK")
    assert unhandled == []


@pytest.mark.parametrize(
    "source",
    [
        {"content": 5},
        {"content": "x", "metadata": "m"},
        {"content": "x", "media_type": "application/pdf"},
        {"content": "x", "author": "agt_nobody"},
        ["content"],
    ],
)
def test_malformed_sources_are_rejected(kdb: KernelDB, agent: str, source: Any) -> None:
    with pytest.raises(Rejected):
        kdb.ingest(source, agent)


@pytest.mark.parametrize(
    "cite",
    [
        {"sentences": []},
        {"sentences": "x"},
        {"sentences": ["just text"]},
        {"sentences": [{"text": "x", "edges": "edg_1"}]},
        {"sentences": [{"text": "x", "claims": [5]}]},
        {"sentences": [{"text": 5}]},
    ],
)
def test_malformed_cites_are_rejected(kdb: KernelDB, agent: str, cite: Any) -> None:
    with pytest.raises(Rejected):
        kdb.cite(cite, agent)

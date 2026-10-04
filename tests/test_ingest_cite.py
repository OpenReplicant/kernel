"""kernel.ingest_source (chunks, spans, content-hash skipping) and kernel.cite."""

from __future__ import annotations

import pytest

from tests.conftest import KernelDB, Rejected

MARKDOWN = """# Invoice approval

Accounts payable receives invoices by email.
A clerk enters each invoice into the ERP.

## Roles

Dana Ruiz is the invoice approver.

```
# not a heading inside code
```
"""


def test_chunks_have_exact_spans_and_heading_paths(kdb: KernelDB, agent: str) -> None:
    result = kdb.ingest({"content": MARKDOWN, "media_type": "text/markdown", "title": "Process"}, agent)
    assert not result["skipped"] and len(result["chunks"]) == 2
    for chunk in result["chunks"]:
        assert MARKDOWN[chunk["char_start"] : chunk["char_end"]] == chunk["text"]
    assert result["chunks"][0]["heading"] == "Invoice approval"
    assert result["chunks"][1]["heading"] == "Invoice approval > Roles"
    assert "# not a heading" in result["chunks"][1]["text"]
    assert result["chunks"][0]["id"] == f"chk_{result['source_id'].split('_')[1]}_0000"


def test_long_text_is_packed_into_bounded_chunks(kdb: KernelDB, agent: str) -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * 120 for i in range(12)]
    content = "\r\n\r\n".join(paragraphs) + "\n" + "x" * 5000
    result = kdb.ingest({"content": content}, agent)
    chunks = result["chunks"]
    assert len(chunks) > 3
    for c in chunks:
        assert content[c["char_start"] : c["char_end"]] == c["text"]
        assert c["char_end"] - c["char_start"] <= 3000
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))


def test_known_content_hash_is_skipped(kdb: KernelDB, agent: str) -> None:
    first = kdb.ingest({"content": "Dana approves invoices."}, agent)
    again = kdb.ingest({"content": "Dana approves invoices.", "title": "copy"}, agent)
    assert again["skipped"] and again["source_id"] == first["source_id"]
    assert again["chunks"] == first["chunks"]
    assert kdb.one("SELECT count(*) FROM kernel.sources") == 1


def test_turns_in_a_collection_are_distinct_by_uri(kdb: KernelDB, agent: str) -> None:
    a = kdb.ingest({"content": "Yes.", "collection": "conv-1", "uri": "turn:1"}, agent)
    b = kdb.ingest({"content": "Yes.", "collection": "conv-1", "uri": "turn:2"}, agent)
    c = kdb.ingest({"content": "Yes.", "collection": "conv-1", "uri": "turn:2"}, agent)
    assert a["source_id"] != b["source_id"] and c["skipped"] and c["source_id"] == b["source_id"]


def test_ingest_rejections(kdb: KernelDB, agent: str) -> None:
    for source in (
        {"content": ""},
        {"content": "x", "media_type": "application/pdf"},
        {"content": "x", "pages": 3},
        {"content": "x", "author": "agt_nobody"},
    ):
        with pytest.raises(Rejected) as err:
            kdb.ingest(source, agent)
        assert err.value.problem in ("payload", "reference")


def test_cite_resolves_edges_and_claims_to_assertions(kdb: KernelDB, agent: str) -> None:
    hr = kdb.source(agent, "HR: Dana approves invoices.")
    mail = kdb.source(agent, "Mail: Dana approves invoices.")
    result = kdb.claim(
        agent,
        "Dana approves invoices",
        [
            {"op": "create", "ref": "$d", "type": "Agent", "kind": "human", "name": "Dana"},
            {"op": "create", "ref": "$r", "kind": "role", "namespace": "bpm", "name": "Invoice approver"},
            {"op": "assert", "ref": "$e", "edge": "implements", "from": "$d", "to": "$r"},
        ],
        source=hr,
    )
    edge = result["refs"]["$e"]
    second = kdb.claim(agent, "Dana approves invoices", [{"op": "assert", "edge_id": edge}], source=mail)
    cited = kdb.cite(
        {
            "answer_id": "ans-1",
            "sentences": [
                {"text": "Dana approves invoices.", "edges": [edge]},
                {"text": "This was stated by HR.", "claims": [result["claim_id"]]},
                {"text": "Nobody disputes it."},
            ],
        },
        agent,
    )
    assert cited["answer_id"] == "ans-1" and cited["traced_share"] == pytest.approx(0.6667)
    edge_assertions = set(cited["cites"][0]["assertion_ids"])
    assert edge_assertions == {
        r[0] for r in kdb.q("SELECT id FROM kernel.assertions WHERE target_id = %s", [edge])
    }
    assert second["claim_id"] in {
        r[0]
        for r in kdb.q("SELECT claim_id FROM kernel.assertions WHERE id = ANY(%s)", [list(edge_assertions)])
    }
    assert cited["cites"][2]["assertion_ids"] == []
    with pytest.raises(Rejected):
        kdb.cite({"answer_id": "ans-1", "sentences": [{"text": "again"}]}, agent)
    with pytest.raises(Rejected) as err:
        kdb.cite({"sentences": [{"text": "x", "edges": ["edg_missing"]}]}, agent)
    assert err.value.problem == "reference"


def test_history_merges_writes_ingestions_and_cites(kdb: KernelDB, agent: str) -> None:
    chunk = kdb.source(agent, "Finance pays on Fridays.")
    result = kdb.claim(agent, "Finance pays on Fridays", [], source=chunk)
    kdb.cite({"sentences": [{"text": "Finance pays on Fridays.", "claims": [result["claim_id"]]}]}, agent)
    kinds = [r[0] for r in kdb.q("SELECT kind FROM kernel.history ORDER BY recorded_at")]
    assert kinds == ["write", "ingest", "write", "cite"]

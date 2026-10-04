"""Quotes and extraction runs.

Quotes: a reported claim names the words of its chunk it rests on, the kernel finds them
(spacing, quote marks, dashes and case may differ, and a quote may run over the chunk's
edge), records their span and refuses a quote that is not there with the nearest sentence.

Runs: an extraction run is an Event that starts by citing its source; claims in it come
from its agent and cite that source; closing it retracts, from the same source, what older
runs found and it did not, never what claims outside runs or newer runs said; the log keeps
only kernel vocabulary, and replay reproduces the graph.
"""

from __future__ import annotations

from typing import Any

import pytest

from kernel.testing import KernelDB, Rejected

DOC = (
    "# Architecture\n\nPayments depends on the Ledger service. Payments depends on Fraud checks.\n\n"
    "Checkout depends on Payments — “the critical path”, says the team."
)


def reported(kdb: KernelDB, agent: str, chunk: str, quote: str | None, **claim: Any) -> dict[str, Any]:
    payload_claim = {
        "text": "Payments needs the ledger.",
        "source": chunk,
        "basis": "reported",
        "modality": "descriptive",
    }
    if quote is not None:
        payload_claim["quote"] = quote
    payload_claim.update(claim)
    return kdb.write({"claim": payload_claim, "read_at_offset": kdb.head(), "ops": []}, agent)


@pytest.fixture
def doc(kdb: KernelDB, agent: str) -> dict[str, Any]:
    return kdb.ingest(
        {"content": DOC, "media_type": "text/markdown", "title": "Architecture", "collection": "arch"}, agent
    )


@pytest.mark.parametrize(
    "quote",
    [
        "Payments depends on the Ledger service",
        "payments  depends on\nthe ledger SERVICE",  # spacing and case
        'Checkout depends on Payments - "the critical path"',  # straight for typographic dash and quotes
    ],
)
def test_a_quote_is_found_and_its_span_recorded(
    kdb: KernelDB, agent: str, doc: dict[str, Any], quote: str
) -> None:
    result = reported(kdb, agent, doc["chunks"][0]["id"], quote)
    start, end, shown = kdb.q(
        "SELECT c.quote_start, c.quote_end, v.quote FROM kernel.claims c "
        "JOIN kernel.claims_view v USING (id) WHERE c.id = %s",
        [result["claim_id"]],
    )[0]
    assert DOC[start:end] == shown
    assert shown.lower().split()[0] == quote.lower().split()[0]


def test_reported_claims_need_a_quote_that_is_there(kdb: KernelDB, agent: str, doc: dict[str, Any]) -> None:
    chunk = doc["chunks"][0]["id"]
    with pytest.raises(Rejected) as err:
        reported(kdb, agent, chunk, None)
    assert (err.value.problem, err.value.rule) == ("provenance", "core.reported_needs_quote")
    with pytest.raises(Rejected) as err:
        reported(kdb, agent, chunk, "Payments depends on the Billing service")
    assert (err.value.problem, err.value.rule) == ("provenance", "kernel.quote_in_source")
    assert err.value.detail["nearest"] == "Payments depends on the Ledger service."
    for bad in ("Paymen", "x" * 1001):
        with pytest.raises(Rejected) as err:
            reported(kdb, agent, chunk, bad)
        assert err.value.problem == "payload"
    # Observed and inferred claims need no quote, but one they give is checked.
    reported(kdb, agent, chunk, None, basis="observed")
    with pytest.raises(Rejected):
        reported(kdb, agent, chunk, "not in the chunk at all", basis="observed")


def test_a_quote_may_run_over_the_chunk_edge(kdb: KernelDB, agent: str) -> None:
    first = ("Filler sentence number one. " * 52) + "The last sentence of the first part."
    content = first + "\n\nThe second part begins here."
    chunks = kdb.ingest({"content": content, "title": "Long note"}, agent)["chunks"]
    assert len(chunks) == 2
    reported(kdb, agent, chunks[0]["id"], "the first part. The second part begins")
    reported(kdb, agent, chunks[1]["id"], "the first part. The second part begins")


def test_redaction_masks_the_quote(kdb: KernelDB, agent: str, doc: dict[str, Any]) -> None:
    result = reported(kdb, agent, doc["chunks"][0]["id"], "Payments depends on Fraud checks")
    kdb.claim(agent, "redact it", [{"op": "redact", "claim": result["claim_id"]}], basis="observed")
    assert kdb.q("SELECT quote, quote_start FROM kernel.claims_view WHERE id = %s", [result["claim_id"]]) == [
        (None, None)
    ]


# Extraction runs ----------------------------------------------------------------------------


class Extractor:
    """Writes claims about components the way an extraction run does."""

    def __init__(self, kdb: KernelDB, agent: str, chunk: str) -> None:
        self.kdb, self.agent, self.chunk = kdb, agent, chunk
        self.ids: dict[str, str] = {}

    def start(self, name: str) -> str:
        result = self.write(
            {"text": f"{name} reads the architecture note.", "basis": "observed"},
            [
                {
                    "op": "create",
                    "ref": "$r",
                    "type": "Event",
                    "kind": "extraction",
                    "name": name,
                    "props": {"skill": "core", "model": "test"},
                }
            ],
        )
        return str(result["refs"]["$r"])

    def depends(self, run: str | None, a: str, b: str, quote: str, promote: bool = False) -> dict[str, Any]:
        ops: list[dict[str, Any]] = []
        for n in (a, b):
            if n not in self.ids:
                ops.append({"op": "create", "ref": f"${n}", "kind": "component", "name": n})
        ops.append(
            {
                "op": "assert",
                "edge": "depends_on",
                "from": self.ids.get(a, f"${a}"),
                "to": self.ids.get(b, f"${b}"),
            }
        )
        if promote:
            ops.append({"op": "promote", "about": [self.ids.get(a, f"${a}")]})
        claim = {"text": f"{a} depends on {b}.", "quote": quote, "basis": "reported"}
        if run:
            claim["run"] = run
        result = self.write(claim, ops)
        self.ids.update({k[1:]: v for k, v in result["refs"].items() if k[1:] in (a, b)})
        return result

    def close(self, run: str) -> dict[str, Any]:
        return self.write(
            {"text": "The run is complete.", "basis": "observed"}, [{"op": "close_run", "run": run}]
        )

    def write(
        self, claim: dict[str, Any], ops: list[dict[str, Any]], agent: str | None = None
    ) -> dict[str, Any]:
        payload = {
            "claim": {"source": self.chunk, "modality": "descriptive", **claim},
            "read_at_offset": self.kdb.head(),
            "ops": ops,
        }
        return self.kdb.write(payload, agent or self.agent)


def belief(kdb: KernelDB, a: str, b: str) -> str:
    return str(
        kdb.one(
            "SELECT e.belief_status FROM kernel.edges e JOIN kernel.nodes f ON f.id = e.from_id "
            "JOIN kernel.nodes t ON t.id = e.to_id WHERE f.name = %s AND t.name = %s",
            [a, b],
        )
    )


def test_closing_a_run_retracts_what_older_runs_found_and_it_did_not(
    kdb: KernelDB, agent: str, doc: dict[str, Any]
) -> None:
    x = Extractor(kdb, agent, doc["chunks"][0]["id"])
    first = x.start("run 1")
    assert kdb.one("SELECT status FROM kernel.nodes WHERE id = %s", [first]) == "ongoing"
    x.depends(first, "Payments", "Ledger", "Payments depends on the Ledger service")
    finding = x.depends(first, "Payments", "Fraud", "Payments depends on Fraud checks", promote=True)
    # A claim outside any run, from the same source: runs never retract it.
    x.depends(None, "Checkout", "Payments", "Checkout depends on Payments")
    closed = x.close(first)
    assert [op["op"] for op in closed["ops"]] == ["transition"]
    assert kdb.one("SELECT status FROM kernel.nodes WHERE id = %s", [first]) == "completed"

    second = x.start("run 2")
    x.depends(second, "Payments", "Ledger", "Payments depends on the Ledger service")
    closed = x.close(second)
    retracted = [
        (op["target"], op.get("edge_id") or op.get("claim_node"))
        for op in closed["ops"]
        if op["op"] == "assert"
    ]
    assert all(op.get("polarity") == -1 for op in closed["ops"] if op["op"] == "assert")
    # The finding run 2 did not report: its claim node, its depends_on edge and its about edge.
    assert ("claim", finding["claim_id"]) in retracted and len(retracted) == 3
    assert belief(kdb, "Payments", "Ledger") == "accepted"
    assert belief(kdb, "Payments", "Fraud") == "rejected"
    assert belief(kdb, "Checkout", "Payments") == "accepted"
    assert (
        kdb.one("SELECT belief_status FROM kernel.nodes WHERE id = %s", [finding["claim_id"]]) == "rejected"
    )
    # The log holds the resolved negative assertions and the transition, never close_run.
    assert (
        kdb.one("SELECT count(*) FROM kernel.log, jsonb_array_elements(ops) o WHERE o ->> 'op' = 'close_run'")
        == 0
    )

    # A third run that finds Fraud again restores it; closing it retracts nothing new.
    third = x.start("run 3")
    x.depends(third, "Payments", "Ledger", "Payments depends on the Ledger service")
    x.depends(third, "Payments", "Fraud", "Payments depends on Fraud checks")
    assert [op["op"] for op in x.close(third)["ops"]] == ["transition"]
    assert belief(kdb, "Payments", "Fraud") == "accepted"


def test_an_older_run_closing_late_does_not_retract_a_newer_runs_findings(
    kdb: KernelDB, agent: str, doc: dict[str, Any]
) -> None:
    x = Extractor(kdb, agent, doc["chunks"][0]["id"])
    older = x.start("older")
    newer = x.start("newer")
    x.depends(newer, "Payments", "Ledger", "Payments depends on the Ledger service")
    x.close(newer)
    x.depends(older, "Payments", "Fraud", "Payments depends on Fraud checks")
    assert [op["op"] for op in x.close(older)["ops"]] == ["transition"]
    assert belief(kdb, "Payments", "Ledger") == "accepted"


def test_runs_belong_to_their_agent_and_source(kdb: KernelDB, agent: str, doc: dict[str, Any]) -> None:
    x = Extractor(kdb, agent, doc["chunks"][0]["id"])
    run = x.start("run")
    other = kdb.register("other-agent")
    elsewhere = kdb.source(agent, "Payments depends on Ledger, says another note.")
    cases = [
        (
            lambda: x.write({"text": "x", "basis": "observed", "run": run}, [], agent=other),
            "kernel.run_agent",
        ),
        (
            lambda: x.write({"text": "x", "basis": "observed", "run": run, "source": elsewhere}, []),
            "kernel.run_source",
        ),
        (lambda: x.write({"text": "x", "basis": "observed", "run": "evt_nope"}, []), None),
        (
            lambda: kdb.write(
                {
                    "claim": {"text": "no source", "basis": "observed", "modality": "descriptive"},
                    "read_at_offset": kdb.head(),
                    "ops": [{"op": "create", "type": "Event", "kind": "extraction", "name": "r"}],
                },
                agent,
            ),
            "kernel.run_source",
        ),
    ]
    for attempt, rule in cases:
        with pytest.raises(Rejected) as err:
            attempt()
        assert err.value.rule == rule
    x.close(run)
    with pytest.raises(Rejected) as err:
        x.close(run)
    assert err.value.rule == "kernel.run_open"


def test_replay_reproduces_a_graph_with_runs_and_quotes(
    kdb: KernelDB, agent: str, doc: dict[str, Any], dbname: str
) -> None:
    from evals.replay import replay

    x = Extractor(kdb, agent, doc["chunks"][0]["id"])
    first = x.start("run 1")
    x.depends(first, "Payments", "Ledger", "Payments depends on the Ledger service")
    x.depends(first, "Payments", "Fraud", "Payments depends on Fraud checks", promote=True)
    x.close(first)
    second = x.start("run 2")
    x.depends(second, "Payments", "Ledger", "Payments depends on the Ledger service")
    x.close(second)
    entries, lines = replay(dbname)
    assert entries == kdb.head()
    assert lines == []

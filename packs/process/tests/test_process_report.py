"""The discovery report (ADR 0034), through the gateway.
- Northwind's report: the map in flow order, every source's stance where the views disagree,
  the measures and the ranking. It writes nothing and names nobody.
- Every sentence about a fact cites the claims of the assertions that count for it, as the
  log holds them, and each footnote gives the words the claim quotes.
- Once a person is erased, their words read [erased]; their stance still counts.
- Footnotes are numbered in order of first use; a sentence that states no fact cites none."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from test_process_rank import ORDER
from test_process_views import CONFIG, LUCIA, MAYA, RULE, assemble

from kernel.testing import KernelDB
from wmk_process import config, conform, report
from wmk_process.compare import Said
from wmk_process.conform import Flow, Model, Node
from wmk_process.report import GROUPS, Line, Report, Section

pytestmark = pytest.mark.anyio

TITLES = [
    "The process as mapped",
    "Where the views disagree",
    *GROUPS.values(),
    "Measures",
    "What to automate first",
]
SOP_RULE = (
    "Requests over 10,000 euros go to the finance controller for approval before a purchase order is created"
)


def sources(result: Report, label: str) -> list[Line]:
    """The lines under a fact where the views disagree: each source's stance."""
    lines = [line for title in GROUPS.values() for line in result.section(title).lines]
    at = [line.text for line in lines].index(label)
    found = []
    for line in lines[at + 1 :]:
        if line.depth == 0:
            break
        found.append(line)
    return found


async def test_the_report_cites_every_fact(gateway: Any, kdb: KernelDB) -> None:
    cfg = await assemble(gateway)
    head = kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0]
    result = await report.read(gateway, cfg)
    assert kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0] == head == result.offset
    assert [s.title for s in result.sections] == TITLES

    # The map, in flow order from the step nothing leads to.
    mapped = [line.text.split(" [")[0] for line in result.section(TITLES[0]).lines[1:] if line.depth == 0]
    assert mapped == [
        "Submit purchase request",
        "Check budget",
        "Review purchase request",
        "Approve purchase request",
        "Create purchase order",
        "Over 10,000 euros? (gateway)",
        "Reject purchase request",
        "Revise purchase request",
        "Send purchase order",
    ]
    assert result.section(TITLES[0]).lines[2].text == (
        "To Check budget [contested]: as written states it; as told and as done deny it."
    )

    # Who approves large requests, from every source, in its own words.
    rule = sources(result, f"{RULE} [contested]")
    assert [line.text for line in rule] == [
        "As written, sop:fin-007, states it.",
        f"As told, {MAYA}, denies it.",
        f"As told, {LUCIA}, states it from 2026-08-01.",
        "As done, conformance:purchase-requests, denies it.",
    ]
    said = [result.claims[line.claims[0]] for line in rule]
    assert [s.basis for s in said] == ["reported", "reported", "reported", "observed"]
    assert said[0].quote == SOP_RULE
    assert said[1].quote and said[1].quote.startswith("In practice, when something urgent comes in")
    assert said[2].quote and said[2].quote.startswith("Since I started in August, every request over 10,000")
    # The log names the requests that skipped the controller.
    assert said[3].quote is None and "6 of the 36 times 'amount > 10000' held" in said[3].text
    assert result.section(GROUPS["denied"]).lines == [Line("None.", bullet=False)]

    # Every sentence about a fact cites the claims whose assertions on it count, at their offsets.
    lines = [line for s in result.sections for line in s.lines]
    for line in lines:
        assert bool(line.edges) == bool(line.claims), line.text
        if not line.edges:
            continue
        rows = kdb.q(
            "SELECT DISTINCT claim_id, log_offset FROM kernel.assertions "
            "WHERE target_type = 'edge' AND target_id = ANY(%s) AND claim_id = ANY(%s)",
            [list(line.edges), list(line.claims)],
        )
        assert sorted(rows) == sorted((c, result.claims[c].offset) for c in line.claims), line.text
        counted = kdb.q(
            "SELECT DISTINCT a.claim_id FROM unnest(%s::text[]) e, "
            "unnest(kernel.counted_assertions('edge', e, %s)) x JOIN kernel.assertions a ON a.id = x",
            [list(line.edges), result.offset],
        )
        assert set(line.claims) <= {c for (c,) in counted}, line.text

    # The measures and the ranking, as rank gives them.
    measured = [line.text for line in result.section("Measures").lines]
    assert measured[0] == (
        "From 2026-01-02 until 2026-09-30, Purchase requests had 120 cases, and the median cycle time, "
        "from a case's first event to its last, was 3.0 days."
    )
    assert "No executions of Check budget are measured." in measured
    assert (
        "Review purchase request ran 137 times, 17 of them repeats; it came a median of 23.1 hours after "
        "the case's previous event, 3418.2 hours in total; 137 times it followed another role's step."
    ) in measured
    assert "Submit purchase request ran 120 times, none of them repeats." in measured
    ranked = [line for line in result.section("What to automate first").lines if line.number]
    assert [(line.number, line.text.split(":")[0]) for line in ranked] == [
        (n, name) for n, (name, _) in enumerate(ORDER, start=1)
    ]
    assert ranked[1].text == (
        "Approve purchase request: 3.84 (volume 0.26, waiting 0.57, rework 0.00, handoffs 1.00, rule 1.00, "
        "system 1.00)."
    )

    # The text: Markdown with footnotes numbered in order of first use, each one used.
    text = result.text()
    refs = result.refs()
    assert list(refs.values()) == list(range(1, len(refs) + 1))
    assert re.findall(r"^\[\^(\d+)\]: ", text, re.M) == [str(n) for n in refs.values()]
    assert text.startswith(
        "# Discovery report: Purchase requests\n\n"
        f"Read from the World Model Kernel at log offset {result.offset}, from these views:\n"
        "- as written: sop:fin-007\n"
    )
    lucia = rule[2].claims[0]
    assert f"  - As told, {LUCIA}, states it from 2026-08-01. [^{refs[lucia]}]\n" in text
    assert (
        f"[^{refs[lucia]}]: as told, {LUCIA}, reported at log offset {said[2].offset}: “Since I started in "
        "August, every request over 10,000 euros comes to me after the line manager's review”\n"
    ) in text
    assert text.count("\n1. Review purchase request: 4.12 ") == 1
    # Claims are attributed to collections, never to people.
    for word in ("Maya", "Lucia", "@northwind"):
        assert word not in text

    dumped = json.dumps(result.to_json())
    for word in ("Maya", "Lucia", "@northwind"):
        assert word not in dumped
    data = json.loads(dumped)
    assert [s["title"] for s in data["sections"]] == TITLES
    assert [s["ref"] for s in data["sources"]] == list(refs.values())
    assert data["sources"][refs[lucia] - 1]["claim_id"] == lucia
    at = [line.text for line in result.section(GROUPS["contested"]).lines].index(f"{RULE} [contested]")
    stance = data["sections"][2]["sentences"][at + 1]
    assert stance["claims"] == list(rule[0].claims) and stance["refs"] == [refs[rule[0].claims[0]]]


async def test_an_erased_persons_words_read_erased(gateway: Any, kdb: KernelDB) -> None:
    cfg = await assemble(gateway)
    maya = kdb.q("SELECT id FROM kernel.nodes WHERE identity ->> 'email' = 'maya.chen@northwind.test'")[0][0]
    kdb.erase({"subject": maya, "requested_by": "ticket-1", "approved_by": "dpo-1"})
    result = await report.read(gateway, cfg)
    hers = [s for s in result.claims.values() if s.collection == MAYA]
    assert hers and all((s.text, s.quote) == ("[erased]", None) for s in hers)
    # Her stance still counts; her words are gone.
    rule = sources(result, f"{RULE} [contested]")
    assert rule[1].text == f"As told, {MAYA}, denies it."
    text = result.text()
    assert f"{MAYA}, reported at log offset {result.claims[rule[1].claims[0]].offset}: [erased]\n" in text
    assert "In practice" not in text


async def test_the_report_needs_the_process_mapped(gateway: Any) -> None:
    with pytest.raises(conform.ModelError, match="no process named 'Purchase requests'"):
        await report.read(gateway, config.load(CONFIG))


def test_footnotes_are_numbered_by_first_use() -> None:
    claims = {
        "c1": Said("c1", 5, "sop:x", "reported", "Text.", "the words"),
        "c2": Said("c2", 9, "interview:a", "reported", "[erased]"),
        "c3": Said("c3", 12, "log:x", "observed", "A step ran\n3 times."),
    }
    views = {"as written": ("sop:x",), "as told": ("interview:a",), "as done": ("log:x",)}
    lines = [
        Line("Each fact.", bullet=False),
        Line("A [contested]"),
        Line("As told, interview:a, denies it.", ("e1",), ("c2",), depth=1),
        Line("As written, sop:x, states it.", ("e1",), ("c1",), depth=1),
        Line("B: 1.00.", ("e2",), ("c3", "c1"), number=1),
        Line("Settle first: A [contested].", ("e1",), ("c1", "c2"), depth=1),
    ]
    result = Report("P", 20, views, [Section("Facts", lines)], claims)
    assert result.refs() == {"c2": 1, "c1": 2, "c3": 3}
    assert result.text().split("\n", 9)[-1] == (
        "## Facts\n"
        "\n"
        "Each fact.\n"
        "\n"
        "- A [contested]\n"
        "  - As told, interview:a, denies it. [^1]\n"
        "  - As written, sop:x, states it. [^2]\n"
        "1. B: 1.00. [^2][^3]\n"
        "   - Settle first: A [contested]. [^1][^2]\n"
        "\n"
        "[^1]: as told, interview:a, reported at log offset 9: [erased]\n"
        "[^2]: as written, sop:x, reported at log offset 5: “the words”\n"
        "[^3]: as done, log:x, observed at log offset 12: A step ran 3 times.\n"
    )


def test_steps_follow_the_flows() -> None:
    names = zip("abcd", "DCBA", strict=True)
    nodes = {k: Node(k, name, "activity", f"p{k}", "accepted") for k, name in names}
    # c starts (nothing leads to it) and leads to d; a and b only lead to each other.
    flows = [Flow("f1", "c", "d", None), Flow("f2", "a", "b", None), Flow("f3", "b", "a", None)]
    assert report.flow_order(Model("p", "P", 1, nodes, flows)) == ["c", "d", "b", "a"]

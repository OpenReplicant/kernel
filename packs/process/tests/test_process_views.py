"""Northwind's three views (ADR 0032), through the gateway.
- The views fixture (the SOP and two interviews) plays, and the log maps onto its nodes.
- `compare` gives the discovery: who approves large requests, the budget check nobody
  does, the late approvals nobody wrote down. It writes nothing and names nobody.
- A collection's stance is its latest assertion: a later retraction reads as a denial, and
  a view whose collections disagree is divided.
- The configuration names the views; the view as done defaults to the log's."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.player import Player, fixtures
from kernel.testing import KernelDB
from wmk_adapter.apply import apply
from wmk_process import compare, config, conform, discover
from wmk_process.compare import Fact, Stance

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
CONFIG = FIXTURES / "northwind-purchase-requests" / "northwind.yaml"
RULE = "Over 10,000 euros? -> Approve purchase request, when amount > 10000"
WRITTEN, TOLD, DONE = "as written", "as told", "as done"
MAYA, LUCIA = "interview:nw-2026-09-15-a", "interview:nw-2026-09-16-b"


async def assemble(gateway: Any) -> config.Config:
    """What `make northwind` does: the views, then the log and its conformance on their nodes."""
    player = Player(gateway, fixtures(["northwind-views"])[0])
    await player.run()
    assert player.failures == [] and player.rejections == 0
    cfg = config.load(CONFIG)
    log = cfg.read()
    mapped = await apply(gateway, discover.build(cfg, log))
    # The log meets the views on the same process, steps, roles and system; it adds the
    # case object and its KPIs.
    assert mapped.failures == [] and (mapped.reused, mapped.created) == (13, 35)
    checked = await apply(gateway, conform.build(cfg, log, await conform.read_model(gateway, cfg)))
    assert checked.failures == [] and checked.denied == 3
    return cfg


def stances(fact: Fact) -> tuple[str, str, str]:
    return fact.stance(WRITTEN), fact.stance(TOLD), fact.stance(DONE)


async def test_the_interview_turns_are_sealed_under_their_speakers_keys(gateway: Any, kdb: KernelDB) -> None:
    await assemble(gateway)
    rows = kdb.q(
        "SELECT s.collection, count(*), bool_and(s.subjects = ARRAY[s.author_agent_id]), "
        "bool_and(starts_with(s.content, 'wmk:sealed:')) FROM kernel.sources s "
        "WHERE starts_with(s.collection, 'interview:') GROUP BY 1 ORDER BY 1"
    )
    assert rows == [(MAYA, 4, True, True), (LUCIA, 3, True, True)]
    names = kdb.q(
        "SELECT n.name, n.identity ->> 'email' FROM kernel.sources s "
        "JOIN kernel.nodes n ON n.id = s.author_agent_id "
        "WHERE starts_with(s.collection, 'interview:') GROUP BY 1, 2 ORDER BY 1"
    )
    assert names == [
        ("Lucia Ferreira", "lucia.ferreira@northwind.test"),
        ("Maya Chen", "maya.chen@northwind.test"),
    ]


async def test_compare_gives_the_discovery(gateway: Any, kdb: KernelDB) -> None:
    cfg = await assemble(gateway)
    head = kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0]
    result = await compare.read(gateway, cfg)
    assert kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0] == head == result.offset
    assert list(result.views) == [WRITTEN, TOLD, DONE]
    assert result.views[DONE] == ("event-log:purchase-requests", "conformance:purchase-requests")

    # Who approves large requests: the SOP and Lucia (from when she started) say every one
    # goes to the controller; Maya and the log say some do not.
    assert [f.label for f in result.group("contested")] == [
        "Check budget",
        "Check budget -> Review purchase request",
        RULE,
        "Submit purchase request -> Check budget",
    ]
    rule = result.fact(RULE)
    assert (rule.belief, stances(rule)) == ("contested", ("asserts", "divided", "denies"))
    assert sorted(rule.views[TOLD], key=lambda s: s.collection) == [
        Stance(MAYA, False),
        Stance(LUCIA, True, "2026-08-01"),
    ]
    # The log names the requests that skipped the controller: five before Lucia started, and
    # one ordered the day after (PR-1092, approved four days later). A question for the next
    # interview, which the log alone cannot settle.
    log = cfg.read()
    ordered = {
        cid: b.time
        for cid, case in log.cases.items()
        if float(case.attrs["amount"]) > 10000
        for a, b in pairwise(case.events)
        if (a.activity, b.activity) == ("PR_REVIEW", "PO_CREATE")
    }
    assert len(ordered) == 6
    assert {c: t.date().isoformat() for c, t in ordered.items() if t >= datetime(2026, 8, 1, tzinfo=UTC)} == {
        "PR-1092": "2026-08-02"
    }

    # The budget check: written, denied by the controller, never seen in the log.
    assert stances(result.fact("Check budget")) == ("asserts", "denies", "denies")
    assert stances(result.fact("Check budget -> Review purchase request")) == ("asserts", "denies", "silent")
    # Late approvals and requests straight to review: told and seen, written nowhere.
    for label in (
        "Send purchase order -> Approve purchase request",
        "Submit purchase request -> Review purchase request",
    ):
        fact = result.fact(label)
        assert (fact.belief, fact.group, stances(fact)) == (
            "accepted",
            "agreed",
            ("silent", "asserts", "asserts"),
        )
    # Where all three meet.
    for label in (
        "Approve purchase request -> Create purchase order",
        "Over 10,000 euros? -> Create purchase order, when else",
    ):
        assert stances(result.fact(label)) == ("asserts", "asserts", "asserts")
    # The gateway only the SOP names, and the log's flows that pass it by (the log never sees
    # a decision, only the step after it).
    assert [f.label for f in result.group("one view")] == [
        "Over 10,000 euros? (gateway)",
        "Review purchase request -> Approve purchase request",
        "Review purchase request -> Create purchase order",
    ]
    assert result.group("denied") == [] and result.group("unstated") == []

    text = result.text()
    assert (
        f"- {RULE} [contested]: as written asserts; as told divided "
        f"({MAYA} denies, {LUCIA} asserts from 2026-08-01); as done denies.\n" in text
    )
    # It reads operations, never the sealed claims: no names, no words of the interviews.
    for word in ("Maya", "Lucia", "urgent", "Coupa shows"):
        assert word not in text

    def source(collection: str) -> dict[str, Any]:
        return {"collection": collection, "holds": True, "valid_from": None, "valid_to": None}

    # The log's digest and its conformance digest both assert the step: one view, two sources.
    assert result.to_json()["facts"][0] == {
        "edge_id": result.facts[0].edge_id,
        "kind": "step",
        "fact": "Approve purchase request",
        "belief": "accepted",
        "group": "agreed",
        "views": {
            WRITTEN: {"stance": "asserts", "sources": [source("sop:fin-007")]},
            DONE: {
                "stance": "asserts",
                "sources": [source("event-log:purchase-requests"), source("conformance:purchase-requests")],
            },
        },
    }


async def test_compare_needs_the_process_mapped(gateway: Any) -> None:
    with pytest.raises(conform.ModelError, match="no process named 'Purchase requests'"):
        await compare.read(gateway, config.load(CONFIG))


def op(edge_id: str, polarity: int, valid_from: str | None = None) -> dict[str, Any]:
    return {
        "op": "assert",
        "target": "edge",
        "edge_id": edge_id,
        "polarity": polarity,
        "valid_from": valid_from,
    }


def test_a_collections_stance_is_its_latest_assertion() -> None:
    entries = [
        {"offset": 1, "ops": [op("e1", 1, "2026-08-01T00:00:00+00:00"), op("e2", 1), op("other", 1)]},
        # A later run's retraction of e1, and an assertion on a claim, not an edge.
        {
            "offset": 2,
            "ops": [op("e1", -1), {"op": "assert", "target": "claim", "claim_id": "e2", "polarity": -1}],
        },
    ]
    assert compare.stances(entries, "c", {"e1", "e2"}) == {"e1": Stance("c", False), "e2": Stance("c", True)}
    assert compare.stances(entries[:1], "c", {"e1"}) == {"e1": Stance("c", True, "2026-08-01")}


def test_facts_fall_into_one_group() -> None:
    def fact(**views: list[Stance]) -> Fact:
        return Fact("e", "flow", "A -> B", None, ("1",), {k.replace("_", " "): v for k, v in views.items()})

    yes, no = Stance("c1", True), Stance("c2", False)
    assert fact(as_written=[yes], as_done=[no]).group == "contested"
    divided = fact(as_told=[yes, no])
    assert (divided.group, divided.stance("as told"), divided.stance("as done")) == (
        "contested",
        "divided",
        "silent",
    )
    assert fact(as_told=[no]).group == "denied"
    assert fact(as_told=[yes, Stance("c3", True)]).group == "one view"
    assert fact(as_told=[yes], as_done=[yes]).group == "agreed"
    assert fact().group == "unstated"
    assert fact(as_told=[Stance("c1", True, "2026-08-01", "2026-09-01")]).text(["as told"]) == (
        "- A -> B [unknown]: as told asserts from 2026-08-01 until 2026-09-01."
    )


def test_the_views_are_configured(tmp_path: Path) -> None:
    cfg = config.load(CONFIG)
    assert cfg.compared() == {
        WRITTEN: ("sop:fin-007",),
        TOLD: (MAYA, LUCIA),
        DONE: ("event-log:purchase-requests", "conformance:purchase-requests"),
    }
    raw = yaml.safe_load(CONFIG.read_text())
    raw["views"] = {"as done": ["event-log:other"]}
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "purchase-requests.csv").write_text("case_id,activity,timestamp\n")
    (tmp_path / "views.yaml").write_text(yaml.safe_dump(raw))
    assert config.load(tmp_path / "views.yaml").compared() == {DONE: ("event-log:other",)}
    for bad in ({"as told": "interview:a"}, {"as told": []}, ["as told"]):
        raw["views"] = bad
        (tmp_path / "views.yaml").write_text(yaml.safe_dump(raw))
        with pytest.raises(config.ConfigError, match="views must map"):
            config.load(tmp_path / "views.yaml")

"""Ranking what to automate (ADR 0033), through the gateway.
- Northwind's steps ranked on what the kernel holds: the measures the log gave, the system,
  and the routing rule the views disagree on. It writes nothing and names nobody.
- Every input is an edge the kernel holds, with its belief and window.
- On the log alone there is no rule to break and nothing unmeasured.
- A KPI's latest value is used, never a retracted one; weights come from the configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from test_process_views import CONFIG, RULE, assemble

from kernel.testing import KernelDB
from wmk_adapter.apply import apply
from wmk_process import config, conform, discover, rank
from wmk_process.conform import Flow, Model, Node

pytestmark = pytest.mark.anyio

ORDER = [
    ("Review purchase request", 4.12),
    ("Approve purchase request", 3.84),
    ("Create purchase order", 3.47),
    ("Revise purchase request", 2.34),
    ("Send purchase order", 2.22),
    ("Submit purchase request", 1.88),
    ("Reject purchase request", 1.05),
]


def order(result: rank.Ranking) -> list[tuple[str, float]]:
    return [(s.node.name, s.score) for s in result.steps]


def labels(refs: list[rank.Ref]) -> list[str]:
    return [r.label for r in refs]


async def test_rank_says_what_to_automate_first(gateway: Any, kdb: KernelDB) -> None:
    cfg = await assemble(gateway)
    head = kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0]
    result = await rank.read(gateway, cfg)
    assert kdb.q("SELECT max(log_offset) FROM kernel.log")[0][0] == head == result.offset
    assert order(result) == ORDER

    # The review is where most time goes: every request waits for it, some twice.
    review = result.step("Review purchase request")
    assert {f: round(p, 4) for f, p in review.points.items()} == {
        "volume": 1.0,
        "waiting": 1.0,
        "rework": round(17 / 137, 4),
        "handoffs": 1.0,
        "rule": 0.0,
        "system": 1.0,
    }
    assert labels(review.unsettled) == ["Check budget -> Review purchase request"]
    # The approval is slow, and the rule that routes large requests to it is contested: the SOP
    # and the controller state it, the procurement lead and the log deny it. Settle that first.
    approve = result.step("Approve purchase request")
    assert (approve.points["rule"], labels(approve.rules), labels(approve.unsettled)) == (1.0, [RULE], [RULE])
    assert approve.measures["median_time"].value == 52.2
    # The budget check is written down and denied, never measured.
    assert [s.node.name for s in result.unmeasured] == ["Check budget"]
    assert labels(result.unmeasured[0].unsettled) == [
        "Check budget",
        "Check budget -> Review purchase request",
        "Submit purchase request -> Check budget",
    ]
    assert [(d.label, [b.text() for b in d.branches]) for d in result.decisions] == [
        (
            "Over 10,000 euros? (gateway)",
            [
                "when amount > 10000, to Approve purchase request [contested]",
                "when else, to Create purchase order [accepted]",
            ],
        )
    ]

    # Every input is a measures edge the kernel holds, accepted, over the log's period.
    used = [m for s in result.steps for m in s.measures.values()]
    assert len(used) == 7 + 7 + 6 * 3
    edges = dict(
        kdb.q(
            "SELECT id, (props ->> 'value')::float FROM kernel.edges "
            "WHERE edge = 'monitors' AND kind = 'measures' AND belief_status = 'accepted'"
        )
    )
    assert all(edges[m.edge_id] == m.value for m in used)
    assert {(m.valid_from, m.valid_to) for m in used} == {("2026-01-02T00:00:00Z", "2026-09-30T00:00:00Z")}
    assert result.window() == ("2026-01-02", "2026-09-30")

    text = result.text()
    assert text.startswith(
        f"What to automate in Purchase requests, ranked at log offset {result.offset}\n"
        "Weights: volume 1, waiting 1, rework 1, handoffs 1, rule 1, system 1 (a score of at most 6)\n"
        "Measured from 2026-01-02 until 2026-09-30\n\n"
        "1. Review purchase request: 4.12\n"
        "   volume    1.00  137 executions\n"
        "   waiting   1.00  3418.2 hours in total since the case's previous event, median 23.1\n"
    )
    assert f"   rule      1.00  {RULE} [contested]\n   system    1.00  Coupa\n   settle first: {RULE}" in text
    assert text.endswith(rank.CLOSING + "\n")
    for word in ("Maya", "Lucia", "urgent"):
        assert word not in text

    top = result.to_json()["steps"][1]
    assert {k: top[k] for k in ("rank", "step", "score", "factors")} == {
        "rank": 2,
        "step": "Approve purchase request",
        "score": 3.84,
        "factors": {
            "volume": round(36 / 137, 4),
            "waiting": round(1958.2 / 3418.2, 4),
            "rework": 0.0,
            "handoffs": 1.0,
            "rule": 1.0,
            "system": 1.0,
        },
    }
    assert (
        top["rules"]
        == top["settle_first"]
        == [{"edge_id": approve.rules[0].edge_id, "fact": RULE, "belief": "contested"}]
    )
    assert top["measures"]["executions"]["value"] == 36.0
    assert top["systems"][0]["fact"] == "Coupa"


async def test_rank_on_the_log_alone(gateway: Any) -> None:
    cfg = config.load(CONFIG)
    assert (await apply(gateway, discover.build(cfg, cfg.read()))).failures == []
    result = await rank.read(gateway, cfg)
    # No rule into the approval, so it falls below creating the order.
    assert order(result) == [
        ("Review purchase request", 4.12),
        ("Create purchase order", 3.47),
        ("Approve purchase request", 2.84),
        ("Revise purchase request", 2.34),
        ("Send purchase order", 2.22),
        ("Submit purchase request", 1.88),
        ("Reject purchase request", 1.05),
    ]
    assert (result.unmeasured, result.decisions) == ([], [])
    assert all(s.unsettled == [] for s in result.steps)


async def test_rank_needs_the_process_mapped(gateway: Any) -> None:
    with pytest.raises(conform.ModelError, match="no process named 'Purchase requests'"):
        await rank.read(gateway, config.load(CONFIG))


def model() -> Model:
    nodes = {
        "a": Node("a", "Check", "activity", "pa", "accepted"),
        "b": Node("b", "Pay", "activity", "pb", "accepted"),
        "c": Node("c", "Archive", "activity", "pc", "rejected"),
    }
    return Model("p", "Payments", 9, nodes, [Flow("f", "a", "b", "amount > 5", "rejected")])


def row(
    kpi: str, step: str, edge: str, value: float, period: str, belief: str = "accepted"
) -> dict[str, Any]:
    return {
        "kpi": kpi,
        "step": step,
        "edge": edge,
        "props": {"value": value, "unit": "x"},
        "valid_from": f"{period}-01T00:00:00Z",
        "valid_to": f"{period}-28T00:00:00Z",
        "belief": belief,
    }


def test_a_kpis_latest_value_is_used_never_a_retracted_one() -> None:
    weights = dict.fromkeys(config.FACTORS, 1.0)
    measures = [
        row("Executions of Check", "a", "m1", 10, "2026-01"),
        row("Executions of Check", "a", "m2", 20, "2026-02"),
        row("executions of  check", "a", "m3", 99, "2026-03", "rejected"),
        row("Executions of Pay", "b", "m4", 5, "2026-02"),
        row("Total time before Pay", "b", "m5", 40.0, "2026-02"),
        row("Executions of Archive", "c", "m6", 50, "2026-02"),
    ]
    result = rank.build(
        model(), weights, measures, [{"step": "b", "system": "Bank", "edge": "s1", "belief": None}]
    )
    check, pay = result.step("Check"), result.step("Pay")
    assert check.measures["executions"].edge_id == "m2"
    # A step whose place in the process is rejected is no step; a rejected rule into Pay breaks.
    assert [s.node.name for s in result.steps] == ["Pay", "Check"]
    assert (pay.points["volume"], pay.points["waiting"], pay.points["rule"], pay.points["system"]) == (
        0.25,
        1.0,
        1.0,
        1.0,
    )
    assert (pay.score, check.score, check.points["waiting"]) == (3.25, 1.0, 0.0)
    assert labels(pay.rules) == ["Check -> Pay, when amount > 5"] and pay.unsettled == []
    # Weights scale each factor; a weight of 0 drops it.
    heavy = rank.build(model(), {**weights, "volume": 3.0, "rule": 0.0}, measures, [])
    assert [(s.node.name, s.score) for s in heavy.steps] == [("Check", 3.0), ("Pay", 1.75)]
    assert "Weights: volume 3, waiting 1, rework 1, handoffs 1, rule 0, system 1 (a score of at most 7)" in (
        heavy.text()
    )


def test_the_weights_are_configured(tmp_path: Path) -> None:
    assert config.load(CONFIG).weights == dict.fromkeys(config.FACTORS, 1.0)
    raw = yaml.safe_load(CONFIG.read_text())
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "purchase-requests.csv").write_text("case_id,activity,timestamp\n")
    raw["rank"] = {"weights": {"waiting": 2, "rule": 0.5}}
    (tmp_path / "rank.yaml").write_text(yaml.safe_dump(raw))
    assert config.load(tmp_path / "rank.yaml").weights == {
        "volume": 1.0,
        "waiting": 2.0,
        "rework": 1.0,
        "handoffs": 1.0,
        "rule": 0.5,
        "system": 1.0,
    }
    for bad in (
        {"weights": {"speed": 1}},
        {"weights": {"rule": -1}},
        {"weights": {"rule": True}},
        {"top": 3},
        [1],
    ):
        raw["rank"] = bad
        (tmp_path / "rank.yaml").write_text(yaml.safe_dump(raw))
        with pytest.raises(config.ConfigError, match="rank takes weights"):
            config.load(tmp_path / "rank.yaml")

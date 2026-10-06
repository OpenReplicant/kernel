"""The event-log adapter against a kernel, through the gateway.
- The fixture maps to the expected graph, and mapping it again writes nothing.
- A newer export retracts what it no longer shows.
- Conformance makes the written view and the log meet: what the SOP says and the log
  denies is contested, and checking again writes nothing."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.compare import compare, produced_graph
from kernel import admin
from kernel.testing import KernelDB
from wmk_adapter.apply import apply
from wmk_process import config, conform, discover

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "northwind-purchase-requests"

SOP = """\
Northwind Foods: Purchase request procedure (SOP-FIN-007, version 3, March 2026)

1. The requester submits a purchase request in Coupa.
2. Finance checks the budget of the requester's cost centre before the line manager reviews the request.
3. The line manager reviews the request, and may send it back for revision or reject it.
4. Requests over 10,000 euros go to the finance controller for approval before a purchase order is created.
5. Requests of 10,000 euros or less go straight to procurement.
6. Procurement creates the purchase order and sends it to the supplier.
"""


def step(ref: str, name: str) -> list[dict[str, Any]]:
    return [
        {
            "op": "create",
            "ref": ref,
            "type": "Entity",
            "kind": "activity",
            "namespace": "process",
            "name": name,
        },
        {"op": "assert", "edge": "part_of", "from": ref, "to": "$process"},
    ]


def flow(frm: str, to: str, when: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "assert", "edge": "flows_to", "from": frm, "to": to}
    if when:
        op["props"] = {"when": when}
    return op


async def call(gateway: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await gateway.call_tool(tool, args)
    body = json.loads(result.content[0].text)
    assert not result.is_error, body
    return body


async def write_sop(gateway: Any) -> None:
    """The view as written: the SOP read by an extractor, one reported claim per sentence."""
    source = await call(
        gateway,
        "ingest_source",
        {
            "content": SOP,
            "media_type": "text/plain",
            "title": "Purchase request procedure (SOP-FIN-007)",
            "uri": "sop:fin-007",
            "collection": "sop:fin-007",
            "origins": ["org:northwind-finance"],
        },
    )
    chunk = source["chunks"][0]["id"]
    head = (await call(gateway, "query_log", {"limit": 1}))["head_offset"]
    sentences = [
        line.split(". ", 1)[1] for line in SOP.splitlines() if line[:2] in {f"{i}." for i in range(1, 7)}
    ]
    writes = [
        [
            {
                "op": "create",
                "ref": "$process",
                "type": "Entity",
                "kind": "process",
                "namespace": "process",
                "name": "Purchase requests",
            },
            *step("$submit", "Submit purchase request"),
        ],
        [
            *step("$budget", "Check budget"),
            *step("$review", "Review purchase request"),
            flow("$submit", "$budget"),
            flow("$budget", "$review"),
        ],
        [
            *step("$revise", "Revise purchase request"),
            *step("$reject", "Reject purchase request"),
            flow("$review", "$revise"),
            flow("$revise", "$review"),
            flow("$review", "$reject"),
        ],
        [
            {
                "op": "create",
                "ref": "$amount",
                "type": "Entity",
                "kind": "gateway",
                "namespace": "process",
                "name": "Over 10,000 euros?",
            },
            {"op": "assert", "edge": "part_of", "from": "$amount", "to": "$process"},
            *step("$approve", "Approve purchase request"),
            *step("$order", "Create purchase order"),
            flow("$review", "$amount"),
            flow("$amount", "$approve", "amount > 10000"),
            flow("$approve", "$order"),
        ],
        [flow("$amount", "$order", "else")],
        [*step("$send", "Send purchase order"), flow("$order", "$send")],
    ]
    batch = [
        {
            "claim": {
                "text": text,
                "source": chunk,
                "quote": text,
                "basis": "reported",
                "modality": "descriptive",
            },
            "ops": ops,
        }
        for text, ops in zip(sentences, writes, strict=True)
    ]
    # Refs from earlier writes in the batch work in later ones.
    await call(gateway, "write_batch", {"writes": batch, "read_at_offset": head})


def edge(
    kdb: KernelDB, frm: str, to: str, edge: str = "flows_to", when: str | None = None
) -> tuple[Any, ...]:
    rows = kdb.q(
        "SELECT e.belief_status, e.origins_for, e.origins_against FROM kernel.edges e "
        "JOIN kernel.nodes f ON f.id = e.from_id JOIN kernel.nodes t ON t.id = e.to_id "
        "WHERE e.edge = %s AND f.name = %s AND t.name = %s AND (e.props ->> 'when') IS NOT DISTINCT FROM %s",
        [edge, frm, to, when],
    )
    assert len(rows) == 1, (frm, to, rows)
    return rows[0]


async def test_the_fixture_maps_to_the_expected_graph_and_again_to_nothing(gateway: Any, dbname: str) -> None:
    cfg = config.load(FIXTURE / "northwind.yaml")
    plan = discover.build(cfg, cfg.read())
    first = await apply(gateway, plan)
    assert first.failures == [] and first.claims == len(plan.timeline()) and first.runs == 1
    expected = yaml.safe_load((FIXTURE / "expected.yaml").read_text())
    result = compare(expected, produced_graph(admin.dsn_for(admin.admin_dsn(), dbname)))
    assert (result.entities.precision, result.entities.recall) == (1.0, 1.0), result.entities
    assert (result.edges.precision, result.edges.recall) == (1.0, 1.0), result.edges
    assert result.attribute_errors == []
    again = await apply(gateway, plan)
    assert (again.claims, again.created, again.unchanged, again.failures) == (0, 0, 1, [])


async def test_a_newer_export_retracts_what_it_no_longer_shows(
    gateway: Any, kdb: KernelDB, tmp_path: Path
) -> None:
    folder = tmp_path / "northwind"
    shutil.copytree(FIXTURE, folder)
    cfg = config.load(folder / "northwind.yaml")
    assert (await apply(gateway, discover.build(cfg, cfg.read()))).failures == []
    # The next export: urgent requests no longer skip the controller (their late approvals
    # are gone), so the order is never followed by an approval.
    log = folder / "log" / "purchase-requests.csv"
    rows = list(csv.DictReader(log.open()))
    late = {r["case_id"] for r in rows if r["activity"] == "PO_SEND"} & {
        r["case_id"] for r in rows if r["activity"] == "PR_APPROVE"
    }
    late = {c for c in late if [r["activity"] for r in rows if r["case_id"] == c][-1] == "PR_APPROVE"}
    assert len(late) == 6
    kept = [r for r in rows if not (r["case_id"] in late and r["activity"] == "PR_APPROVE")]
    with log.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept)
    second = await apply(gateway, discover.build(cfg, cfg.read()))
    assert second.failures == [] and second.runs == 1
    assert edge(kdb, "Send purchase order", "Approve purchase request")[0] == "rejected"
    assert edge(kdb, "Review purchase request", "Approve purchase request")[0] == "accepted"
    # The old KPI values are retracted, the new ones accepted.
    values = kdb.q(
        "SELECT e.props ->> 'value', e.belief_status FROM kernel.edges e "
        "JOIN kernel.nodes k ON k.id = e.from_id "
        "WHERE k.name = 'Executions of Approve purchase request' ORDER BY 1"
    )
    assert values == [("30", "accepted"), ("36", "rejected")]


async def test_conformance_makes_what_the_log_denies_contested(gateway: Any, kdb: KernelDB) -> None:
    cfg = config.load(FIXTURE / "northwind.yaml")
    log = cfg.read()
    await write_sop(gateway)
    mapped = await apply(gateway, discover.build(cfg, log))
    # The log meets the SOP on the same nodes: the steps it names are reused, not duplicated.
    assert mapped.failures == [] and mapped.reused >= 8
    model = await conform.read_model(gateway, cfg)
    assert {x.name for x in model.nodes.values() if x.kind == "gateway"} == {"Over 10,000 euros?"}
    plan = conform.build(cfg, log, model)
    digest = plan.sources[0].content
    assert "- Check budget: never ran." in digest
    assert (
        "- Submit purchase request -> Check budget: never followed, though Submit purchase request ran "
        "120 times." in digest
    )
    assert (
        "- After Review purchase request, when amount > 10000, to Approve purchase request: held 36 times; "
        "30 continued there, 6 elsewhere (Create purchase order 6; cases PR-1019, PR-1020, PR-1035)."
        in digest
    )
    assert (
        "- After Review purchase request, when else, to Create purchase order: held 77 times; all continued "
        "there." in digest
    )
    assert digest.endswith(
        "Not checked:\n- Check budget -> Review purchase request: Check budget never ran.\n"
    )
    checked = await apply(gateway, plan)
    assert checked.failures == [] and checked.denied == 3

    # What the SOP says and the log denies is contested: one origin on each side.
    assert edge(kdb, "Over 10,000 euros?", "Approve purchase request", when="amount > 10000") == (
        "contested",
        1,
        1,
    )
    assert edge(kdb, "Submit purchase request", "Check budget") == ("contested", 1, 1)
    assert edge(kdb, "Check budget", "Purchase requests", "part_of") == ("contested", 1, 1)
    # Where they agree, the SOP and the log are two origins. The log's digest and its
    # conformance digest are two sources from one origin, counted once.
    assert edge(kdb, "Over 10,000 euros?", "Create purchase order", when="else") == ("accepted", 2, 0)
    assert edge(kdb, "Create purchase order", "Send purchase order") == ("accepted", 2, 0)
    # What only the log shows has its support alone: the late approvals, and the request
    # submitted straight to review (the SOP puts a budget check between).
    assert edge(kdb, "Send purchase order", "Approve purchase request") == ("accepted", 1, 0)
    assert edge(kdb, "Submit purchase request", "Review purchase request") == ("accepted", 1, 0)

    # Checking again an unchanged model against an unchanged log writes nothing.
    again = await apply(gateway, conform.build(cfg, log, await conform.read_model(gateway, cfg)))
    assert (again.claims, again.unchanged, again.failures) == (0, 1, [])


async def test_conformance_needs_the_process_mapped(gateway: Any) -> None:
    cfg = config.load(FIXTURE / "northwind.yaml")
    with pytest.raises(conform.ModelError, match="no process named 'Purchase requests'"):
        await conform.read_model(gateway, cfg)

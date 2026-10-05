"""`make eval`: run every fixture through the eval profile and score the produced graph.

For each fixture: a fresh database with the kernel and the reference pack, a gateway
running the eval profile (in-process, spoken to over MCP), the fixture's script played
through the gateway's tools, then precision and recall for entities and edges against the
expected graph, and the replay check on that database.

The script stands in for the eval harness's model, which keeps CI deterministic and free
of model calls; which harness drives the eval profile with a live model is an open
question in the design (docs/design-v1.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from evals.compare import Comparison, compare, produced_graph
from evals.player import Fixture, Player, fixtures
from evals.replay import replay
from gateway import profiles
from gateway.db import Kernel
from gateway.embeddings import NoEmbedder
from gateway.identity import ensure_agents
from gateway.server import build_server
from gateway.tools import Tools
from kernel import admin

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "evals" / "out" / "report.json"
WRITER_PASSWORD = os.environ.get("WMK_WRITER_PASSWORD", "writer")
READER_PASSWORD = os.environ.get("WMK_READER_PASSWORD", "reader")


def login(dbname: str, role: str, password: str) -> str:
    return admin.dsn_for(admin.admin_dsn(), dbname) + f" user={role} password={password}"


async def run_fixture(fixture: Fixture, keep: bool) -> dict[str, Any]:
    dbname = f"wmk_eval_{fixture.name.replace('-', '_')}"
    admin.create_database(dbname)
    admin.apply(dbname, packs=fixture.packs)
    kernel = Kernel(
        login(dbname, "wmk_writer", WRITER_PASSWORD), login(dbname, "wmk_reader", READER_PASSWORD)
    )
    await kernel.open()
    try:
        profile = profiles.load(ROOT / "profiles" / "eval.yaml")
        agents = await ensure_agents(kernel, profile)
        server = build_server(Tools(kernel, NoEmbedder(), agents, profile.name))
        async with Client(server) as client:
            player = Player(client, fixture)
            await player.run()
    finally:
        await kernel.close()
    comparison = compare(fixture.expected, produced_graph(admin.dsn_for(admin.admin_dsn(), dbname)))
    entries, replay_diff = replay(dbname)
    if not keep:
        admin.drop_database(dbname)
    return {
        "fixture": fixture.name,
        "comparison": comparison,
        "step_failures": [asdict(f) for f in player.failures],
        "tool_calls": player.calls,
        "rejections": player.rejections,
        "replay": {"entries": entries, "diff": replay_diff},
    }


def verdict(result: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    c: Comparison = result["comparison"]
    problems = [f"step {f['index']} ({f['tool']}): {f['reason']}" for f in result["step_failures"]]
    for name, score in (("entities", c.entities), ("edges", c.edges)):
        want = thresholds.get(name, {"precision": 1.0, "recall": 1.0})
        if score.precision < want["precision"]:
            problems.append(
                f"{name} precision {score.precision:.3f} < {want['precision']}: unexpected {score.unexpected}"
            )
        if score.recall < want["recall"]:
            problems.append(f"{name} recall {score.recall:.3f} < {want['recall']}: missing {score.missing}")
    if c.attribute_accuracy < thresholds.get("attributes", 1.0):
        problems += c.attribute_errors
    if c.unresolved_expected != c.unresolved_produced:
        problems.append(f"{c.unresolved_produced} unresolved claims, expected {c.unresolved_expected}")
    problems += [f"replay: {line}" for line in result["replay"]["diff"][:10]]
    return problems


def summary_row(result: dict[str, Any]) -> str:
    c: Comparison = result["comparison"]
    return (
        f"{result['fixture']:<20} entities P {c.entities.precision:.2f} R {c.entities.recall:.2f}   "
        f"edges P {c.edges.precision:.2f} R {c.edges.recall:.2f}   attributes {c.attribute_accuracy:.2f}   "
        f"calls {result['tool_calls']}, rejections {result['rejections']}, "
        f"replay {'ok' if not result['replay']['diff'] else 'DIFF'} ({result['replay']['entries']} entries)"
    )


async def main_async(names: list[str] | None, keep: bool) -> int:
    selected = fixtures(names)
    if not selected:
        print("no fixtures found")
        return 1
    admin.ensure_login_roles(WRITER_PASSWORD, READER_PASSWORD)
    failed = False
    report = []
    for fixture in selected:
        result = await run_fixture(fixture, keep)
        problems = verdict(result, fixture.thresholds)
        print(summary_row(result))
        for line in problems:
            print(f"  FAIL {line}")
        failed = failed or bool(problems)
        c: Comparison = result["comparison"]
        report.append(
            {
                **{k: v for k, v in result.items() if k != "comparison"},
                "entities": {
                    "precision": c.entities.precision,
                    "recall": c.entities.recall,
                    "missing": c.entities.missing,
                    "unexpected": c.entities.unexpected,
                },
                "edges": {
                    "precision": c.edges.precision,
                    "recall": c.edges.recall,
                    "missing": c.edges.missing,
                    "unexpected": c.edges.unexpected,
                },
                "attribute_accuracy": c.attribute_accuracy,
                "problems": problems,
            }
        )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print(f"report: {REPORT.relative_to(ROOT)}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the eval fixtures through the eval profile.")
    parser.add_argument("fixtures", nargs="*", help="fixture names (default: all)")
    parser.add_argument("--keep", action="store_true", help="keep each fixture's database")
    args = parser.parse_args()
    sys.exit(anyio.run(main_async, args.fixtures or None, args.keep))


if __name__ == "__main__":
    main()

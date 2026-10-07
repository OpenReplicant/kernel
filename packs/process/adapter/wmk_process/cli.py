"""wmk-process: map an event log into the World Model Kernel, and check the mapped process
against it.

wmk-process intake engagement.yaml --url http://localhost:8000/mcp  # a client's folder (ADR 0036)
wmk-process plan northwind.yaml                    # the claims discover would write, as a fixture script
wmk-process discover northwind.yaml --url http://localhost:8000/mcp
wmk-process conform northwind.yaml --url http://localhost:8000/mcp
wmk-process compare northwind.yaml --url http://localhost:8000/mcp   # where the views disagree
wmk-process rank northwind.yaml --url http://localhost:8000/mcp      # what to automate first
wmk-process report northwind.yaml --url http://localhost:8000/mcp    # all of it, with sources (Markdown)
wmk-process snapshot northwind.yaml packs/process/evals/fixtures/<name>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anyio
from mcp import Client

from wmk_adapter.apply import ApplyError, apply
from wmk_adapter.script import script_text
from wmk_process import compare, conform, discover, fixture, intake, rank, report
from wmk_process.config import Config, ConfigError, load
from wmk_process.logs import LogError


def main() -> None:
    parser = argparse.ArgumentParser(description="Map an event log into the World Model Kernel.")
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("intake", help="ingest an engagement folder, map its exports, list the work left")
    take.add_argument("engagement", type=Path, help="the engagement file")
    take.add_argument("--url", default="http://localhost:8000/mcp", help="the gateway's MCP endpoint")
    take.add_argument(
        "--work", type=Path, help="write the sources waiting to be mapped, with their chunks, here"
    )
    sub.add_parser("plan", help="print the claims discover would write").add_argument("config", type=Path)
    for name, text in (
        ("discover", "write the log's view of the process through the gateway"),
        ("conform", "check the mapped process against the log and write the verdicts"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("config", type=Path)
        p.add_argument("--url", default="http://localhost:8000/mcp", help="the gateway's MCP endpoint")
        p.add_argument("--force", action="store_true", help="write claims even when the digest is unchanged")
    for name, text in (
        ("compare", "where the configured views of the process agree and disagree"),
        ("rank", "the process's steps ranked by where automation would pay"),
        ("report", "the discovery report: the map, the disagreements, the measures and the ranking, sourced"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("config", type=Path)
        p.add_argument("--url", default="http://localhost:8000/mcp", help="the gateway's MCP endpoint")
        p.add_argument("--json", action="store_true", help="print JSON instead of text")
    snap = sub.add_parser("snapshot", help="write an eval fixture from a log")
    snap.add_argument("config", type=Path)
    snap.add_argument("fixture", type=Path, help="the fixture folder")
    snap.add_argument("--name", help="fixture name (default: the folder name)")
    args = parser.parse_args()

    try:
        if args.command == "intake":
            sys.exit(anyio.run(_intake, args.url, intake.load(args.engagement), args.work))
        cfg = load(args.config)
        if args.command in ("compare", "rank", "report"):
            sys.exit(anyio.run(_read, args.command, args.url, cfg, args.json))
        log = cfg.read()
        if args.command == "plan":
            print(script_text(discover.build(cfg, log), "wmk-process plan"), end="")
        elif args.command == "snapshot":
            plan = fixture.write(args.config, args.fixture, name=args.name or args.fixture.name)
            print(f"{args.fixture}: {len(plan.facts)} claims; write expected.yaml by hand")
        else:
            sys.exit(anyio.run(_run, args.command, args.url, cfg, args.force))
    except (ConfigError, LogError, intake.EngagementError) as exc:
        sys.exit(str(exc))


async def _intake(url: str, eng: intake.Engagement, work: Path | None) -> int:
    try:
        async with Client(url) as client:
            result = await intake.run(client, eng)
    except (ApplyError, conform.ModelError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    print(result.text(), end="")
    if work is not None:
        work.write_text(result.work())
        print(f"Wrote {len(result.waiting)} sources waiting to be mapped to {work}.")
    return 1 if result.failures else 0


async def _run(command: str, url: str, cfg: Config, force: bool) -> int:
    log = cfg.read()
    try:
        async with Client(url) as client:
            if command == "discover":
                plan = discover.build(cfg, log)
            else:
                plan = conform.build(cfg, log, await conform.read_model(client, cfg))
            report = await apply(client, plan, force=force)
    except (ApplyError, conform.ModelError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    print(report.summary())
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 1 if report.failures else 0


async def _read(command: str, url: str, cfg: Config, as_json: bool) -> int:
    try:
        async with Client(url) as client:
            result: compare.Comparison | rank.Ranking | report.Report
            if command == "compare":
                result = await compare.read(client, cfg)
            elif command == "rank":
                result = await rank.read(client, cfg)
            else:
                result = await report.read(client, cfg)
    except conform.ModelError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result.to_json(), indent=2))
    else:
        print(result.text(), end="")
    return 0


if __name__ == "__main__":
    main()

"""wmk-software: map a repository into the World Model Kernel.

wmk-software plan .                         # the claims it would write, as a fixture script
wmk-software map . --url http://localhost:8000/mcp [--self "World Model Kernel (this instance)"]
wmk-software snapshot . packs/software/evals/fixtures/kernel-stack --rev origin/main --self "..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio
from mcp import Client

from wmk_software import fixture
from wmk_software.apply import ApplyError, apply
from wmk_software.mapping import build
from wmk_software.plan import Plan
from wmk_software.repo import RepoError, read


def main() -> None:
    parser = argparse.ArgumentParser(description="Map a repository into the World Model Kernel.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("repo", type=Path, help="the repository folder")
        p.add_argument("--rev", help="map this commit instead of the working tree")
        p.add_argument("--url-of-repo", dest="repo_url", help="the repository's URL (default: origin)")
        p.add_argument("--history", type=int, default=50, help="first-parent commits to map")
        p.add_argument("--self", dest="self_system", metavar="NAME", help="also map the self boundary")

    common(sub.add_parser("plan", help="print the claims the adapter would write"))
    mapper = sub.add_parser("map", help="write the repository into a kernel through its gateway")
    common(mapper)
    mapper.add_argument("--url", default="http://localhost:8000/mcp", help="the gateway's MCP endpoint")
    mapper.add_argument("--force", action="store_true", help="write claims even for unchanged files")
    snap = sub.add_parser("snapshot", help="write an eval fixture from a commit")
    common(snap)
    snap.add_argument("fixture", type=Path, help="the fixture folder")
    snap.add_argument("--name", help="fixture name (default: the folder name)")
    args = parser.parse_args()

    try:
        repo = read(args.repo, rev=args.rev, url=args.repo_url, history=args.history)
    except RepoError as exc:
        sys.exit(str(exc))
    if args.command == "plan":
        print(fixture.script_text(build(repo, self_system=args.self_system)), end="")
    elif args.command == "snapshot":
        plan = fixture.write(
            repo, args.fixture, name=args.name or args.fixture.name, self_system=args.self_system
        )
        print(
            f"{args.fixture}: {len(plan.sources)} sources, {len(plan.facts)} claims; "
            "write expected.yaml by hand"
        )
    else:
        sys.exit(anyio.run(_map, args.url, build(repo, self_system=args.self_system), args.force))


async def _map(url: str, plan: Plan, force: bool) -> int:
    try:
        async with Client(url) as client:
            report = await apply(client, plan, force=force)
    except ApplyError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    print(report.summary())
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    main()

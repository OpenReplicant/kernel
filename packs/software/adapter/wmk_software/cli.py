"""wmk-software: map a repository into the World Model Kernel, and observe what runs.

What a repository declares:
wmk-software plan .                         # the claims it would write, as a fixture script
wmk-software map . --url http://localhost:8000/mcp [--self "World Model Kernel (this instance)"]
wmk-software snapshot . packs/software/evals/fixtures/kernel-stack --rev origin/main --self "..."

What runs (ADR 0028):
wmk-software capture wmk -o capture.json [--host dev]   # the containers of a Compose project
wmk-software observe capture.json --url http://localhost:8000/mcp [--repo URL] [--print | --snapshot FIXTURE]
wmk-software drift capture.json --url http://localhost:8000/mcp
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio
from mcp import Client

from wmk_adapter.script import script_text
from wmk_software import docker, drift, fixture, observe
from wmk_software.apply import ApplyError, Report, apply
from wmk_software.mapping import build
from wmk_software.plan import Plan
from wmk_software.repo import RepoError, read, repository_url

REPO_COMMANDS = ("plan", "map", "snapshot")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map a repository, or what runs, into the World Model Kernel."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("repo", type=Path, help="the repository folder")
        p.add_argument("--rev", help="map this commit instead of the working tree")
        p.add_argument("--url-of-repo", dest="repo_url", help="the repository's URL (default: origin)")
        p.add_argument("--history", type=int, default=50, help="first-parent commits to map")
        p.add_argument("--self", dest="self_system", metavar="NAME", help="also map the self boundary")

    def gateway(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default="http://localhost:8000/mcp", help="the gateway's MCP endpoint")

    common(sub.add_parser("plan", help="print the claims the adapter would write"))
    mapper = sub.add_parser("map", help="write the repository into a kernel through its gateway")
    common(mapper)
    gateway(mapper)
    mapper.add_argument("--force", action="store_true", help="write claims even for unchanged files")
    snap = sub.add_parser("snapshot", help="write an eval fixture from a commit")
    common(snap)
    snap.add_argument("fixture", type=Path, help="the fixture folder")
    snap.add_argument("--name", help="fixture name (default: the folder name)")

    cap = sub.add_parser(
        "capture", help="capture the containers of a Compose project from the local Docker daemon"
    )
    cap.add_argument("project", help="the Compose project, such as wmk")
    cap.add_argument("--host", help="the host's name in the kernel (default: the daemon's)")
    cap.add_argument("-o", "--output", type=Path, help="write the capture here (default: stdout)")
    obs = sub.add_parser("observe", help="write what a capture shows running through the gateway")
    obs.add_argument("capture", type=Path)
    gateway(obs)
    obs.add_argument(
        "--repo", help="the repository that declares the stack (its URL or folder), for identity keys"
    )
    obs.add_argument("--print", action="store_true", help="print the claims as a fixture script instead")
    obs.add_argument(
        "--snapshot", type=Path, metavar="FIXTURE", help="write an eval fixture from the capture instead"
    )
    dr = sub.add_parser("drift", help="check what the kernel holds as declared against a capture")
    dr.add_argument("capture", type=Path)
    gateway(dr)
    args = parser.parse_args()

    if args.command not in REPO_COMMANDS:
        sys.exit(runtime(args))
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


def runtime(args: argparse.Namespace) -> int:
    try:
        if args.command == "capture":
            text = docker.capture(args.project, host=args.host).to_json()
            if args.output:
                args.output.write_text(text)
            else:
                print(text, end="")
            return 0
        cap = docker.load(args.capture)
        if getattr(args, "repo", None) and Path(args.repo).is_dir():
            args.repo = repository_url(args.repo)
    except (docker.CaptureError, RepoError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.command == "observe" and args.snapshot:
        plan = fixture.write_observed(args.capture, args.snapshot, name=args.snapshot.name, repo=args.repo)
        print(f"{args.snapshot}: {len(plan.facts)} claims; write expected.yaml by hand")
        return 0
    if args.command == "observe" and args.print:
        print(script_text(observe.build(cap, repo=args.repo), "wmk-software observe --print"), end="")
        return 0
    return anyio.run(_runtime, args.command, args.url, cap, getattr(args, "repo", None))


async def _runtime(command: str, url: str, cap: docker.Capture, repo: str | None) -> int:
    try:
        async with Client(url) as client:
            if command == "observe":
                plan = observe.build(cap, repo=repo)
            else:
                plan = drift.build(cap, await drift.read_declared(client, cap.project))
            report = await apply(client, plan)
    except (ApplyError, drift.DeclaredError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    return _report(report)


async def _map(url: str, plan: Plan, force: bool) -> int:
    try:
        async with Client(url) as client:
            report = await apply(client, plan, force=force)
    except ApplyError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    return _report(report)


def _report(report: Report) -> int:
    print(report.summary())
    for failure in report.failures:
        print(f"  FAIL {failure}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    main()

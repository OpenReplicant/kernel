"""`make seed`: write the eval fixtures into a running stack through its gateway over HTTP.

This is the bulk path in miniature: many payloads through the gateway's write tool, each
checked by kernel.write like any other. A fixture whose first source a claim already cites
is skipped, so seeding twice changes nothing. A source that is known but cited by no claim,
such as one intake ingested (ADR 0036), is still waiting to be mapped.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

import anyio
from mcp import Client

from evals.player import Player, fixtures


async def seed(url: str, names: list[str] | None) -> int:
    failed = False
    async with Client(url) as client:
        for fixture in fixtures(names):
            if not fixture.seed and not names:
                print(f"{fixture.name}: not seeded (it maps nodes another fixture creates)")
                continue
            first = next(i for i, step in enumerate(fixture.steps) if "ingest" in step)
            probe = Player(client, replace(fixture, steps=[fixture.steps[first]]))
            await probe.run()
            if probe.skipped_sources and await cited(client, next(iter(probe.source_ids.values()))):
                print(f"{fixture.name}: already seeded, skipped")
                continue
            player = Player(client, fixture)
            await player.run()
            for failure in player.failures:
                print(f"  FAIL step {failure.index} ({failure.tool}): {failure.reason}")
            failed = failed or bool(player.failures)
            print(f"{fixture.name}: {player.calls} tool calls, {player.rejections} rejections handled")
    return 1 if failed else 0


async def cited(client: Client, source_id: str) -> bool:
    result = await client.call_tool("query_log", {"source_id": source_id, "limit": 1})
    return bool(json.loads(result.content[0].text)["entries"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a running gateway with the eval fixtures.")
    parser.add_argument("fixtures", nargs="*")
    parser.add_argument("--url", default="http://localhost:8000/mcp")
    args = parser.parse_args()
    sys.exit(anyio.run(seed, args.url, args.fixtures or None))


if __name__ == "__main__":
    main()

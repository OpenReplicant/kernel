"""The resolution eval: how well the cascade finds the node a mention denotes.

For each set in evals/resolution/: a fresh database, the known world written through
`kernel.write` (one write path), then every mention resolved with the gateway's
`lookup_entities` tool, as an agent would. Scores:

- auto precision: of mentions whose top candidate is in an auto band (certain, high), the
  share where it is the right node. A wrong auto candidate blocks a create or misleads.
- auto recall: of mentions of a known node, the share resolved to it in an auto band.
- candidate recall: of mentions of a known node, the share with it among the candidates.
- new clean: of new and unclear mentions, the share with no auto-band candidate.
- ambiguous per mention: candidates in the ambiguous band, which the model must judge.

Runs without an embedding endpoint unless WMK_EMBEDDING_URL is set, so CI measures the
name stages; with one, the vector stage joins in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import anyio
import yaml
from mcp import Client

from evals.run import READER_PASSWORD, WRITER_PASSWORD, login
from gateway import profiles
from gateway.db import Kernel
from gateway.embeddings import Embedder, HttpEmbedder, NoEmbedder
from gateway.identity import ensure_agents
from gateway.server import build_server
from gateway.tools import Tools
from kernel import admin

ROOT = Path(__file__).resolve().parent.parent
SETS = ROOT / "evals" / "resolution"
REPORT = ROOT / "evals" / "out" / "resolution.json"
AUTO = ("certain", "high")


@dataclass
class Outcome:
    name: str
    expect: str
    top: str | None
    top_band: str | None
    top_stage: str | None
    found: list[str]
    ambiguous: int
    verdict: str


@dataclass
class Scores:
    auto_precision: float
    auto_recall: float
    candidate_recall: float
    new_clean: float
    ambiguous_per_mention: float
    stages: dict[str, int] = field(default_factory=dict)


def body(result: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(result.content[0].text)
    if result.is_error:
        raise RuntimeError(f"{data.get('type')}: {data.get('detail')}")
    return data


async def load_known(client: Any, known: list[dict[str, Any]]) -> dict[str, str]:
    """Writes the known world one node per claim; distinct nodes the cascade finds close are
    declared distinct, since the set says they are."""
    ids: dict[str, str] = {}
    for node in known:
        op = {"op": "create", "ref": "$n", **{k: v for k, v in node.items() if k != "key"}}
        op.setdefault("type", "Entity")
        head = body(await client.call_tool("query_log", {"limit": 1}))["head_offset"]
        payload = {
            "claim": {"text": f"{node['name']} exists", "basis": "observed", "modality": "descriptive"},
            "read_at_offset": head,
            "ops": [op],
        }
        result = await client.call_tool("write", payload)
        data = json.loads(result.content[0].text)
        if result.is_error and data.get("candidates"):
            op["distinct_from"] = [c["node_id"] for c in data["candidates"]]
            result = await client.call_tool("write", payload)
            data = json.loads(result.content[0].text)
        if result.is_error:
            raise RuntimeError(f"known node {node['key']}: {data.get('detail')}")
        ids[node["key"]] = data["refs"]["$n"]
    return ids


def judge(mention: dict[str, Any], candidates: list[dict[str, Any]], keys: dict[str, str]) -> Outcome:
    found = [keys.get(c["node_id"], c["node_id"]) for c in candidates]
    top = candidates[0] if candidates else None
    top_key = found[0] if found else None
    auto = top is not None and top["band"] in AUTO
    expect = mention["expect"]
    if expect in ("new", "unclear"):
        among = set(mention.get("among", []))
        verdict = "wrong-auto" if auto else ("ok" if among <= set(found) else "missed")
    elif auto:
        verdict = "auto" if top_key == expect else "wrong-auto"
    else:
        verdict = "candidate" if expect in found else "missed"
    return Outcome(
        name=mention["name"],
        expect=expect,
        top=top_key,
        top_band=top["band"] if top else None,
        top_stage=top["stage"] if top else None,
        found=found,
        ambiguous=sum(1 for c in candidates if c["band"] == "ambiguous"),
        verdict=verdict,
    )


def score(outcomes: list[Outcome]) -> Scores:
    known = [o for o in outcomes if o.expect not in ("new", "unclear")]
    other = [o for o in outcomes if o.expect in ("new", "unclear")]
    autos = [o for o in outcomes if o.top_band in AUTO]
    stages: dict[str, int] = {}
    for o in known:
        if o.verdict == "auto" and o.top_stage:
            stages[o.top_stage] = stages.get(o.top_stage, 0) + 1

    def share(part: int, whole: int) -> float:
        return round(part / whole, 3) if whole else 1.0

    return Scores(
        auto_precision=share(sum(o.verdict == "auto" for o in autos), len(autos)),
        auto_recall=share(sum(o.verdict == "auto" for o in known), len(known)),
        candidate_recall=share(sum(o.verdict in ("auto", "candidate") for o in known), len(known)),
        new_clean=share(sum(o.verdict != "wrong-auto" for o in other), len(other)),
        ambiguous_per_mention=round(sum(o.ambiguous for o in outcomes) / len(outcomes), 2),
        stages=stages,
    )


async def run_set(path: Path, embedder: Embedder, keep: bool) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text())
    dbname = f"wmk_resolution_{spec['name']}"
    admin.create_database(dbname)
    admin.apply(dbname, packs=spec.get("packs", []))
    kernel = Kernel(
        login(dbname, "wmk_writer", WRITER_PASSWORD), login(dbname, "wmk_reader", READER_PASSWORD)
    )
    await kernel.open()
    try:
        profile = profiles.load(ROOT / "profiles" / "eval.yaml")
        agents = await ensure_agents(kernel, profile)
        async with Client(build_server(Tools(kernel, embedder, agents, profile.name))) as client:
            ids = await load_known(client, spec["known"])
            keys = {v: k for k, v in ids.items()}
            queries = [
                {k: m[k] for k in ("name", "type", "kind", "identity") if k in m} for m in spec["mentions"]
            ]
            results = body(await client.call_tool("lookup_entities", {"queries": queries}))["results"]
    finally:
        await kernel.close()
        if not keep:
            admin.drop_database(dbname)
    outcomes = [judge(m, r["candidates"], keys) for m, r in zip(spec["mentions"], results, strict=True)]
    scores = score(outcomes)
    floors = spec.get("floors", {})
    problems = [
        f"{name.replace('_', ' ')} {getattr(scores, name):.3f} < floor {floor}"
        for name, floor in floors.items()
        if getattr(scores, name) < floor
    ]
    problems += [
        f"{o.name!r}: top {o.top} ({o.top_band}), expected {o.expect}"
        for o in outcomes
        if o.verdict == "wrong-auto"
    ]
    return {"set": spec["name"], "scores": scores, "outcomes": outcomes, "problems": problems}


def summary_row(result: dict[str, Any]) -> str:
    s: Scores = result["scores"]
    return (
        f"{result['set']:<20} auto P {s.auto_precision:.2f} R {s.auto_recall:.2f}   "
        f"candidates R {s.candidate_recall:.2f}   new clean {s.new_clean:.2f}   "
        f"ambiguous/mention {s.ambiguous_per_mention:.2f}   "
        f"by stage {', '.join(f'{k} {v}' for k, v in sorted(s.stages.items())) or '-'}"
    )


async def main_async(names: list[str] | None, keep: bool, verbose: bool) -> int:
    paths = sorted(SETS.glob("*.yaml"))
    if names:
        paths = [p for p in paths if p.stem in names]
    if not paths:
        print("no resolution sets found")
        return 1
    admin.ensure_login_roles(WRITER_PASSWORD, READER_PASSWORD)
    url = os.environ.get("WMK_EMBEDDING_URL")
    embedder: Embedder = (
        HttpEmbedder(url, os.environ.get("WMK_EMBEDDING_MODEL", ""), os.environ.get("WMK_EMBEDDING_API_KEY"))
        if url
        else NoEmbedder()
    )
    report, failed = [], False
    for path in paths:
        result = await run_set(path, embedder, keep)
        print(summary_row(result))
        for o in result["outcomes"]:
            if verbose or o.verdict in ("missed", "wrong-auto"):
                print(
                    f"  {o.verdict:<10} {o.name!r} -> {o.top} ({o.top_band}, {o.top_stage}); "
                    f"expected {o.expect}"
                )
        for line in result["problems"]:
            print(f"  FAIL {line}")
        failed = failed or bool(result["problems"])
        report.append(
            {
                "set": result["set"],
                "scores": asdict(result["scores"]),
                "outcomes": [asdict(o) for o in result["outcomes"]],
                "problems": result["problems"],
            }
        )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    print(f"report: {REPORT.relative_to(ROOT)}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the resolution cascade on the resolution sets.")
    parser.add_argument("sets", nargs="*", help="set names (default: all)")
    parser.add_argument("--keep", action="store_true", help="keep each set's database")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every mention's outcome")
    args = parser.parse_args()
    sys.exit(anyio.run(main_async, args.sets or None, args.keep, args.verbose))


if __name__ == "__main__":
    main()

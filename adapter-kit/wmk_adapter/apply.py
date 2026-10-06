"""Playing a plan through the kernel's gateway as an MCP client: the bulk path, one claim per
`write`, each checked by kernel.write like any other.

- Every source is ingested. A file whose content is already known (same collection, same
  content) is skipped with its claims, so mapping an unchanged repository writes nothing.
- Nodes that exist are reused: by identity key when the node has one, else by normalised
  name. New nodes are created, listing near-namesakes in `distinct_from`.
- A changed file is mapped in an extraction run (ADR 0020). Closing the run makes the kernel
  retract what an older run over the file found and this one did not; a run with a refused
  claim is cancelled instead, so a partial pass retracts nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from wmk_adapter.plan import SELF, Fact, Plan, Slugs, Source


class ApplyError(Exception):
    """The gateway refused a read the adapter depends on."""


@dataclass
class Report:
    sources: int = 0
    unchanged: int = 0
    claims: int = 0
    created: int = 0
    reused: int = 0
    runs: int = 0
    retracted: int = 0
    denied: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        denied = f"{self.denied} denied, " if self.denied else ""
        return (
            f"{self.sources} sources ({self.unchanged} unchanged), {self.claims} claims written in "
            f"{self.runs} runs, {self.created} nodes created, {self.reused} reused, "
            f"{self.retracted} retracted, {denied}{len(self.failures)} failures"
        )


def chunk_at(spans: list[tuple[int, int, str]], offset: int) -> str:
    """The chunk holding character `offset`, else the last chunk starting before it."""
    best = spans[0][2]
    for start, end, chunk in spans:
        if start <= offset < end:
            return chunk
        if start <= offset:
            best = chunk
    return best


class Applier:
    def __init__(self, client: Any, plan: Plan, *, force: bool = False) -> None:
        self.client = client
        self.plan = plan
        self.force = force
        self.report = Report()
        self.ids: dict[str, str] = {}
        self.existing: set[str] = set()  # nodes that were there before this run
        self.spans: dict[str, list[tuple[int, int, str]]] = {}
        self.changed: set[str] = set()
        self.failed: set[str] = set()
        self.head = 0
        self.agent: str | None = None
        self.slugs = Slugs()

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        result = await self.client.call_tool(tool, args)
        return bool(result.is_error), json.loads(result.content[0].text)

    async def run(self) -> Report:
        for source in self.plan.sources:
            await self.ingest(source)
        for fact, run in self.plan.timeline():
            await self.write(fact, run)
        return self.report

    async def ingest(self, source: Source) -> None:
        self.report.sources += 1
        error, result = await self.call("ingest_source", source.ingest_args())
        if error:
            self.fail(source.alias, f"ingest {source.alias}: {result.get('detail')}")
            return
        self.spans[source.alias] = [(c["char_start"], c["char_end"], c["id"]) for c in result["chunks"]]
        if result["skipped"] and not self.force:
            self.report.unchanged += 1
        else:
            self.changed.add(source.alias)

    async def resolve(self, keys: list[str]) -> dict[str, list[str]]:
        """Map existing nodes into self.ids; return distinct_from ids for the nodes to create."""
        unknown = [k for k in keys if k not in self.ids and not self.plan.is_run(k)]
        fresh = [k for k in keys if k not in self.ids and self.plan.is_run(k)]
        if fresh:
            # A run is never reused: each pass over a file is a run of its own.
            unknown += fresh
        if not unknown:
            return {}
        queries = [
            {f: spec[f] for f in ("name", "type", "kind", "identity") if f in spec}
            for spec in (self.plan.nodes[k] for k in unknown)
        ]
        error, result = await self.call("lookup_entities", {"queries": queries, "limit": 5})
        if error:
            raise ApplyError(f"lookup_entities: {result.get('detail')}")
        self.head = max(self.head, int(result["head_offset"]))
        distinct: dict[str, list[str]] = {}
        for key, found in zip(unknown, result["results"], strict=True):
            has_identity = bool(self.plan.nodes[key].get("identity"))
            same = next(
                (
                    c
                    for c in found["candidates"]
                    if c["stage"] == "identity" or (not has_identity and c["stage"] == "normalized")
                ),
                None,
            )
            if same and not self.plan.is_run(key):
                self.ids[key] = same["node_id"]
                self.existing.add(key)
                self.report.reused += 1
            else:
                distinct[key] = [
                    c["node_id"] for c in found["candidates"] if c["band"] in ("certain", "high")
                ]
        return distinct

    async def write(self, fact: Fact, run: str | None = None) -> None:
        if fact.source is not None and fact.source not in self.changed:
            return
        closing = any(op["op"] == "close_run" for op in fact.ops)
        if closing and fact.source in self.failed:
            # Some claims of this pass were refused: cancel the run rather than close it.
            run_key = fact.ops[0]["run"]
            fact = type(fact)(
                f"{fact.text.split(' has ')[0]} stopped mapping {fact.source}: some claims were refused, "
                "so nothing is retracted.",
                source=fact.source,
                ops=[{"op": "transition", "node": run_key, "status": "cancelled"}],
            )
        keys = fact.node_keys()
        distinct = await self.resolve(keys)
        if fact.once and all(k in self.existing for k in keys):
            return
        created: dict[str, str] = {}
        ops = self.render(fact, distinct, created)
        claim = fact.claim()
        if fact.source is not None:
            claim["source"] = chunk_at(self.spans[fact.source], fact.at)
        if run is not None:
            if run not in self.ids:
                self.fail(fact.source, f"{fact.text[:90]}: its run was not started")
                return
            claim["run"] = self.ids[run]
        payload = {"claim": claim, "read_at_offset": self.head, "ops": ops}
        error, result = await self.call("write", payload)
        if error and str(result.get("type", "")).endswith("/stale"):
            # Another writer touched one of these nodes since our last read: read again, retry once.
            payload["read_at_offset"] = await self.head_offset()
            error, result = await self.call("write", payload)
        if error:
            self.fail(fact.source, f"{fact.text[:90]}: {result.get('detail')}")
            return
        self.report.claims += 1
        self.agent = result.get("agent_id") or self.agent
        self.head = int(result["offset"])
        for key, slug in created.items():
            self.ids[key] = result["refs"][f"${slug}"]
            if self.plan.is_run(key):
                self.report.runs += 1
            else:
                self.report.created += 1
        if closing:
            self.report.retracted += sum(
                1 for op in result["ops"] if op["op"] == "assert" and op.get("polarity") == -1
            )
        else:
            self.report.denied += sum(
                1 for op in fact.ops if op["op"] == "assert" and op.get("polarity") == "negative"
            )

    def render(
        self, fact: Fact, distinct: dict[str, list[str]], created: dict[str, str]
    ) -> list[dict[str, Any]]:
        ops: list[dict[str, Any]] = []

        def ref(key: str) -> str:
            if key == SELF:
                return self.self_agent()
            if key in self.ids:
                return self.ids[key]
            if key not in created:
                created[key] = self.slugs(key)
                op = {"op": "create", "ref": f"${created[key]}", **self.plan.nodes[key]}
                if distinct.get(key):
                    op["distinct_from"] = distinct[key]
                ops.append(op)
            return f"${created[key]}"

        for op in fact.ops:
            if op["op"] == "create":
                ref(op["node"])
            elif op["op"] == "close_run":
                ops.append({"op": "close_run", "run": ref(op["run"])})
            elif op["op"] == "transition":
                ops.append({**op, "node": ref(op["node"])})
            elif "edge_id" in op:
                ops.append(dict(op))
            else:
                ops.append({**op, "from": ref(op["from"]), "to": ref(op["to"])})
        return ops

    def self_agent(self) -> str:
        """The writing agent: from the last write result, else the gateway's instructions."""
        if self.agent is None:
            found = re.search(r"You write as agent (\S+) \(", self.client.instructions or "")
            if not found:
                raise ApplyError("could not tell which agent writes through this gateway")
            self.agent = found.group(1)
        return self.agent

    async def head_offset(self) -> int:
        error, result = await self.call("query_log", {"limit": 1})
        if error:
            raise ApplyError(f"query_log: {result.get('detail')}")
        return int(result["head_offset"])

    def fail(self, source: str | None, message: str) -> None:
        self.report.failures.append(message)
        if source is not None:
            self.failed.add(source)


async def apply(client: Any, plan: Plan, *, force: bool = False) -> Report:
    return await Applier(client, plan, force=force).run()

"""Plays a fixture's script through the MCP gateway, exactly as an agent would call the tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gateway.tools import Claim

ROOT = Path(__file__).resolve().parent.parent
# Fixtures live with what they test: the kernel's in evals/fixtures, each pack's in its own
# evals/fixtures.
FIXTURE_ROOTS = [ROOT / "evals" / "fixtures", *sorted(ROOT.glob("packs/*/evals/fixtures"))]
_TEMPLATE = re.compile(r"\{\{(\w+):([^}]+)\}\}")


@dataclass
class Fixture:
    name: str
    path: Path
    sources: dict[str, dict[str, Any]]
    steps: list[dict[str, Any]]
    expected: dict[str, Any]
    thresholds: dict[str, Any]
    namespaces: list[str] | None = None
    packs: list[str] = field(default_factory=list)
    # False for a fixture whose nodes another fixture also creates: a script cannot reuse
    # them the way an adapter's resolution does, so it is scored in its own database only.
    seed: bool = True
    # True for a script that looks up each node before creating it, as a harness following
    # the core skill does, so that it meets nodes an adapter or intake created first.
    reuse: bool = False

    @classmethod
    def load(cls, path: Path) -> Fixture:
        meta = yaml.safe_load((path / "fixture.yaml").read_text())
        return cls(
            name=meta["name"],
            path=path,
            sources=meta["sources"],
            steps=yaml.safe_load((path / meta.get("script", "script.yaml")).read_text()),
            expected=yaml.safe_load((path / meta.get("expected", "expected.yaml")).read_text()),
            thresholds=meta.get("thresholds", {}),
            namespaces=meta.get("namespaces"),
            packs=list(meta.get("packs") or []),
            seed=bool(meta.get("seed", True)),
            reuse=bool(meta.get("reuse", False)),
        )


def fixtures(names: list[str] | None = None) -> list[Fixture]:
    found = sorted(
        (p for root in FIXTURE_ROOTS for p in root.iterdir() if (p / "fixture.yaml").exists()),
        key=lambda p: p.name,
    )
    loaded = [Fixture.load(p) for p in found]
    return [f for f in loaded if not names or f.name in names]


@dataclass
class StepFailure:
    index: int
    tool: str
    reason: str


@dataclass
class Player:
    client: Any
    fixture: Fixture
    refs: dict[str, str] = field(default_factory=dict)
    chunks: dict[str, list[str]] = field(default_factory=dict)
    spans: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    source_ids: dict[str, str] = field(default_factory=dict)
    agent_id: str | None = None
    failures: list[StepFailure] = field(default_factory=list)
    skipped_sources: list[str] = field(default_factory=list)
    calls: int = 0
    rejections: int = 0

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        self.calls += 1
        result = await self.client.call_tool(tool, args)
        return bool(result.is_error), json.loads(result.content[0].text)

    def render(self, value: Any) -> Any:
        if isinstance(value, str):
            whole = _TEMPLATE.fullmatch(value)
            if whole:
                return self._lookup(whole.group(1), whole.group(2))
            return _TEMPLATE.sub(lambda m: str(self._lookup(m.group(1), m.group(2))), value)
        if isinstance(value, list):
            return [self.render(v) for v in value]
        if isinstance(value, dict):
            return {k: self.render(v) for k, v in value.items()}
        return value

    def _lookup(self, kind: str, key: str) -> str:
        if kind == "ref":
            return self.refs[key]
        if kind == "chunk":
            source, seq = key.rsplit(":", 1)
            return self.chunks[source][int(seq)]
        if kind == "at":
            # The chunk holding a character offset of the source (else the last one before it).
            source, offset = key.rsplit(":", 1)
            spans = self.spans[source]
            held = [c for start, end, c in spans if start <= int(offset) < end]
            before = [c for start, _, c in spans if start <= int(offset)]
            return (held or before or [spans[0][2]])[0 if held else -1]
        if kind == "agent" and key == "self" and self.agent_id:
            return self.agent_id
        raise KeyError(f"{kind}:{key}")

    async def run(self) -> None:
        for index, step in enumerate(self.fixture.steps):
            tool = next(iter(k for k in step if k not in ("expect",)))
            try:
                await getattr(self, f"_{tool}")(index, step)
            except KeyError as exc:
                self.failures.append(StepFailure(index, tool, f"unresolved template {exc}"))

    async def _ingest(self, index: int, step: dict[str, Any]) -> None:
        alias = step["ingest"]
        # A source may name what the script created before it, such as its author.
        spec = self.render(dict(self.fixture.sources[alias]))
        content = (self.fixture.path / spec.pop("file")).read_text()
        error, result = await self.call("ingest_source", {"content": content, **spec})
        if error:
            self.failures.append(StepFailure(index, "ingest_source", result.get("detail", "")))
            return
        if result["skipped"]:
            self.skipped_sources.append(alias)
        self.source_ids[alias] = result["source_id"]
        self.chunks[alias] = [c["id"] for c in result["chunks"]]
        self.spans[alias] = [(c["char_start"], c["char_end"], c["id"]) for c in result["chunks"]]

    async def _write(self, index: int, step: dict[str, Any]) -> None:
        payload = self.render(step["write"])
        # The gateway drops claim keys it does not know. In a script they are a typo, or a YAML
        # flow scalar cut short at a comma, as in 10,000.
        unknown = sorted(set(payload["claim"]) - set(Claim.model_fields))
        if unknown:
            self.failures.append(StepFailure(index, "write", f"unknown claim keys: {', '.join(unknown)}"))
            return
        # Read first: the schema slice for the claim, whose head_offset the write cites.
        args: dict[str, Any] = {"passage": payload["claim"]["text"]}
        if self.fixture.namespaces:
            args["namespaces"] = self.fixture.namespaces
        _, slice_ = await self.call("get_schema_slice", args)
        payload.setdefault("read_at_offset", slice_["head_offset"])
        if self.fixture.reuse:
            reused = await self._reuse(payload)
            if reused is None:
                return
            payload = reused
        error, result = await self.call("write", payload)
        expected = step.get("expect", "accepted")
        if error:
            self.rejections += 1
            want = expected.get("rejected") if isinstance(expected, dict) else None
            if result.get("type") != want:
                self.failures.append(
                    StepFailure(
                        index,
                        "write",
                        f"rejected {result.get('type')} ({result.get('rule')}): {result.get('detail')}",
                    )
                )
            return
        if expected != "accepted":
            self.failures.append(StepFailure(index, "write", f"accepted, expected {expected}"))
        self.agent_id = result.get("agent_id") or self.agent_id
        self.refs.update({k.lstrip("$"): v for k, v in result["refs"].items()})

    async def _reuse(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """The write with each node it creates looked up first: a node with the same identity, or
        without one the same kind and normalised name, is reused instead, as apply resolves
        nodes. Runs are always new. None when every operation was such a create."""
        creates = [
            op for op in payload["ops"] if op.get("op") == "create" and op.get("type", "Entity") != "Event"
        ]
        if not creates:
            return payload
        queries = [{f: op[f] for f in ("name", "type", "kind", "identity") if f in op} for op in creates]
        _, found = await self.call("lookup_entities", {"queries": queries, "limit": 5})
        bound: dict[str, str] = {}
        for op, result in zip(creates, found["results"], strict=True):
            same = next(
                (
                    c["node_id"]
                    for c in result["candidates"]
                    if c["stage"] == "identity" or (not op.get("identity") and c["stage"] == "normalized")
                ),
                None,
            )
            if same:
                bound[op["ref"]] = same
        if not bound:
            return payload
        self.refs.update({ref.lstrip("$"): node for ref, node in bound.items()})
        ops = [
            {k: bound.get(v, v) if isinstance(v, str) else v for k, v in op.items()}
            for op in payload["ops"]
            if not (op.get("op") == "create" and op.get("ref") in bound)
        ]
        return {**payload, "ops": ops} if ops else None

    async def _query(self, index: int, step: dict[str, Any]) -> None:
        query = self.render(step["query"])
        want = query.pop("expect_rows", None)
        error, result = await self.call("query_graph", query)
        if error:
            self.failures.append(StepFailure(index, "query_graph", result.get("detail", "")))
        elif want is not None and len(result["rows"]) != want:
            self.failures.append(
                StepFailure(index, "query_graph", f"{len(result['rows'])} rows, expected {want}")
            )

    async def _cite(self, index: int, step: dict[str, Any]) -> None:
        error, result = await self.call("cite", self.render(step["cite"]))
        if error:
            self.failures.append(StepFailure(index, "cite", result.get("detail", "")))

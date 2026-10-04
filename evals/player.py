"""Plays a fixture's script through the MCP gateway, exactly as an agent would call the tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
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
        )


def fixtures(names: list[str] | None = None) -> list[Fixture]:
    found = sorted(p for p in FIXTURES.iterdir() if (p / "fixture.yaml").exists())
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
        raise KeyError(f"unknown template {kind}:{key}")

    async def run(self) -> None:
        for index, step in enumerate(self.fixture.steps):
            tool = next(iter(k for k in step if k not in ("expect",)))
            try:
                await getattr(self, f"_{tool}")(index, step)
            except KeyError as exc:
                self.failures.append(StepFailure(index, tool, f"unresolved template {exc}"))

    async def _ingest(self, index: int, step: dict[str, Any]) -> None:
        alias = step["ingest"]
        spec = dict(self.fixture.sources[alias])
        content = (self.fixture.path / spec.pop("file")).read_text()
        error, result = await self.call("ingest_source", {"content": content, **spec})
        if error:
            self.failures.append(StepFailure(index, "ingest_source", result.get("detail", "")))
            return
        if result["skipped"]:
            self.skipped_sources.append(alias)
        self.chunks[alias] = [c["id"] for c in result["chunks"]]

    async def _write(self, index: int, step: dict[str, Any]) -> None:
        payload = self.render(step["write"])
        # Read first: the schema slice for the claim, whose head_offset the write cites.
        args: dict[str, Any] = {"passage": payload["claim"]["text"]}
        if self.fixture.namespaces:
            args["namespaces"] = self.fixture.namespaces
        _, slice_ = await self.call("get_schema_slice", args)
        payload.setdefault("read_at_offset", slice_["head_offset"])
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
        self.refs.update({k.lstrip("$"): v for k, v in result["refs"].items()})

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

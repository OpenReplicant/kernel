"""A mapping plan: the sources to ingest and the claims to write, with no database involved.

Facts name nodes by key. `Plan.script()` renders the plan as an eval fixture script, in
which the first fact that mentions a node creates it; `apply` resolves keys against a
live kernel instead, reusing nodes that already exist. A fact may also assert on an edge
that already exists, by its `edge_id` (to deny it or close its window); such facts need a
live kernel and are not rendered as scripts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# The agent that writes the claims, known from the gateway's write results.
SELF = "agent:self"


@dataclass(frozen=True)
class Source:
    """A file (or the git history) as a kernel source. `collection` is stable across
    versions, so a newer version of a file supersedes what the older one said."""

    alias: str
    content: str
    title: str
    uri: str
    collection: str
    media_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)
    # Facts from an append-only source are never retracted when a later version omits them.
    append_only: bool = False
    # Who the content comes from: the files of one repository are not independent of each
    # other, so belief counts their repository once.
    origins: tuple[str, ...] = ()
    # Node keys of the people the content is from or about (human agents): the kernel seals
    # the source under their keys, so erasing one of them makes it unreadable (ADR 0022).
    # `apply` creates them before ingesting; a plan with subjects has no script form.
    subjects: tuple[str, ...] = ()

    def ingest_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "content": self.content,
            "media_type": self.media_type,
            "title": self.title,
            "uri": self.uri,
            "collection": self.collection,
        }
        if self.origins:
            args["origins"] = list(self.origins)
        if self.metadata:
            args["metadata"] = self.metadata
        return args


@dataclass
class Fact:
    """One claim. Its ops are `{"op": "create", "node": key}` (mention a node, creating it if
    needed), `{"op": "assert", "edge": ..., "from": key, "to": key, "props"?: {...}}` and
    `{"op": "assert", "edge_id": ..., "polarity"?: "negative", "valid_to"?: ...}` on an
    existing edge, and `{"op": "transition", "node_id": ..., "status": ...}` on an existing
    node."""

    text: str
    source: str | None = None
    at: int = 0
    basis: str = "observed"
    modality: str = "descriptive"
    confidence: str = "high"
    # Write the fact only when a node it mentions is new: for append-only history and for the
    # self boundary, whose claims need saying once.
    once: bool = False
    ops: list[dict[str, Any]] = field(default_factory=list)

    def node_keys(self) -> list[str]:
        """Node keys in order of first mention."""
        seen: dict[str, None] = {}
        for op in self.ops:
            for k in (op.get("node"), op.get("run"), op.get("from"), op.get("to")):
                if k and k != SELF:
                    seen.setdefault(k)
        return list(seen)

    def claim(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "basis": self.basis,
            "modality": self.modality,
            "confidence": self.confidence,
        }


class Plan:
    def __init__(self, extractor: str, version: str) -> None:
        # Who maps: names the extraction runs and the claims that start and close them.
        self.extractor = extractor
        self.version = version
        self.sources: list[Source] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.facts: list[Fact] = []
        self._asserted: set[tuple[Any, ...]] = set()

    def source(self, source: Source) -> Source:
        self.sources.append(source)
        return source

    def node(self, key: str, **spec: Any) -> str:
        """Register a node's create fields under `key`; the first registration wins."""
        self.nodes.setdefault(key, spec)
        return key

    def fact(self, text: str, **kw: Any) -> Fact:
        fact = Fact(text, **kw)
        self.facts.append(fact)
        return fact

    def mention(self, fact: Fact, key: str) -> None:
        fact.ops.append({"op": "create", "node": key})

    def edge(
        self,
        fact: Fact,
        edge: str,
        frm: str,
        to: str,
        props: dict[str, Any] | None = None,
        *,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> None:
        """Assert an edge in `fact`, once per source: a second identical assertion from the same
        source adds nothing."""
        mark = (fact.source, edge, frm, to, json.dumps(props or {}, sort_keys=True), valid_from, valid_to)
        if fact.source is not None and mark in self._asserted:
            return
        self._asserted.add(mark)
        op: dict[str, Any] = {"op": "assert", "edge": edge, "from": frm, "to": to}
        if props:
            op["props"] = props
        if valid_from:
            op["valid_from"] = valid_from
        if valid_to:
            op["valid_to"] = valid_to
        fact.ops.append(op)

    def transition(self, fact: Fact, node_id: str, status: str) -> None:
        """Move a node that already exists in the kernel, by its id, to a status."""
        fact.ops.append({"op": "transition", "node_id": node_id, "status": status})

    def on_edge(self, fact: Fact, edge_id: str, *, deny: bool = False) -> None:
        """Assert (or deny) an edge that already exists in the kernel, by its id."""
        op: dict[str, Any] = {"op": "assert", "edge_id": edge_id}
        if deny:
            op["polarity"] = "negative"
        fact.ops.append(op)

    def drop_empty(self) -> None:
        """Facts whose every assertion was already made by an earlier fact say nothing new."""
        self.facts = [f for f in self.facts if f.ops]

    # Extraction runs ------------------------------------------------------------------------

    def timeline(self) -> list[tuple[Fact, str | None]]:
        """Facts in writing order, each with the run its claim belongs to. Every source that is
        not append-only is mapped in a run: a fact starting it before its first claim, and one
        closing it after its last, so the kernel retracts what an older run over the same file
        found and this one did not."""
        sources = {s.alias: s for s in self.sources if not s.append_only}
        first: dict[str, int] = {}
        last: dict[str, int] = {}
        for i, fact in enumerate(self.facts):
            if fact.source in sources:
                first.setdefault(fact.source, i)
                last[fact.source] = i
        out: list[tuple[Fact, str | None]] = []
        for i, fact in enumerate(self.facts):
            alias = fact.source
            run = self.run_key(sources[alias]) if alias in first else None
            if run and alias and first[alias] == i:
                out.append((self.start_fact(sources[alias], run), None))
            out.append((fact, run))
            if run and alias and last[alias] == i:
                out.append((self.close_fact(sources[alias], run), None))
        return out

    def run_key(self, source: Source) -> str:
        digest = hashlib.sha256(source.content.encode()).hexdigest()[:12]
        return self.node(
            f"run:{source.alias}",
            type="Event",
            kind="extraction",
            name=f"{self.extractor} over {source.alias} @ {digest}",
            props={"extractor": self.extractor, "version": self.version, "source": source.alias},
        )

    def start_fact(self, source: Source, run: str) -> Fact:
        fact = Fact(f"{self.extractor} {self.version} maps {source.alias}.", source=source.alias)
        self.mention(fact, run)
        return fact

    def close_fact(self, source: Source, run: str) -> Fact:
        fact = Fact(f"{self.extractor} has mapped all of {source.alias}.", source=source.alias)
        fact.ops.append({"op": "close_run", "run": run})
        return fact

    @staticmethod
    def is_run(key: str) -> bool:
        return key.startswith("run:")

    # Rendering as an eval fixture -----------------------------------------------------------

    def script(self) -> list[dict[str, Any]]:
        """The plan as a fixture script for evals/player.py: `{{at:<alias>:<offset>}}` is the
        chunk holding that character, `{{ref:<slug>}}` a node created by an earlier write,
        `{{agent:self}}` the writing agent."""
        if any(s.subjects for s in self.sources):
            raise ValueError("a source with subjects needs a live kernel; it has no script form")
        steps: list[dict[str, Any]] = [{"ingest": s.alias} for s in self.sources]
        slugs = Slugs()
        created: dict[str, str] = {}
        for fact, run in self.timeline():
            local: set[str] = set()
            ops: list[dict[str, Any]] = []

            def ref(key: str, ops: list[dict[str, Any]] = ops, local: set[str] = local) -> str:
                if key == SELF:
                    return "{{agent:self}}"
                if key in created:
                    return f"${created[key]}" if key in local else f"{{{{ref:{created[key]}}}}}"
                slug = slugs(key)
                op = {"op": "create", "ref": f"${slug}", **self.nodes[key]}
                earlier = [k for k in created if k not in local]
                distinct = [f"{{{{ref:{created[k]}}}}}" for k in self.similar(key, earlier)]
                if distinct:
                    op["distinct_from"] = distinct
                ops.append(op)
                created[key] = slug
                local.add(key)
                return f"${slug}"

            for op in fact.ops:
                if op["op"] == "create":
                    ref(op["node"])
                elif op["op"] == "close_run":
                    ops.append({"op": "close_run", "run": ref(op["run"])})
                elif "edge_id" in op or "node_id" in op:
                    raise ValueError(
                        "a fact on an existing edge or node needs a live kernel; it has no script form"
                    )
                else:
                    ops.append({**op, "from": ref(op["from"]), "to": ref(op["to"])})
            claim = fact.claim()
            if fact.source is not None:
                claim["source"] = f"{{{{at:{fact.source}:{fact.at}}}}}"
            if run is not None:
                claim["run"] = ref(run)
            steps.append({"write": {"claim": claim, "ops": ops}})
        return steps

    def similar(self, key: str, among: dict[str, Any] | list[str]) -> list[str]:
        """Earlier nodes of the same type and kind whose names the kernel's trigram stage could
        mistake for this one's (similarity at 0.6 or more; the kernel refuses at 0.85)."""
        spec = self.nodes[key]
        found = []
        for other in among:
            o = self.nodes[other]
            if other == key or (o.get("type"), o.get("kind")) != (spec.get("type"), spec.get("kind")):
                continue
            if similarity(spec["name"], o["name"]) >= 0.6:
                found.append(other)
        return found


class Slugs:
    """Ref names ($slug) for node keys: lower-case letters, digits and underscores, unique."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.by_key: dict[str, str] = {}

    def __call__(self, key: str) -> str:
        if key in self.by_key:
            return self.by_key[key]
        base = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") or "node"
        slug, n = base, 2
        while slug in self.used:
            slug, n = f"{base}_{n}", n + 1
        self.used.add(slug)
        self.by_key[key] = slug
        return slug


def normalize(name: str) -> str:
    """kernel.normalize_name: accents folded, lower case, runs of other characters as one space."""
    folded = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-z]+", " ", folded).strip()


def trigrams(text: str) -> set[str]:
    """pg_trgm's trigrams: each word padded with two spaces before and one after."""
    out: set[str] = set()
    for word in normalize(text).split():
        padded = f"  {word} "
        out |= {padded[i : i + 3] for i in range(len(padded) - 2)}
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0

"""Where the views of a process agree and disagree (ADR 0032).

`read` reads the process as `conform` does: each step's `part_of` edge into it and each
flow out of a step. Then, for every collection of every view the configuration names, it
replays the log entries citing that collection, in order. The latest assertion of a
collection on an edge is its stance, as belief counts it: a source counts once, through its
latest assertion, and a retraction by a later run is a denial. A view asserts a fact when
every collection of it that states the fact asserts it, denies it when every one denies it,
and is divided otherwise. Each fact falls in one group:
- contested: some source asserts it and some denies it;
- denied: some source denies it and none asserts it;
- one view: a single view asserts it and none denies it;
- agreed: two or more views assert it and none denies it;
- unstated: no collection of these views states it (another source does).

It reads through the gateway and writes nothing. Each stance keeps the claim behind it, for
the discovery report (ADR 0034); the comparison itself prints operations only, never claim
text, so it prints nothing sealed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from wmk_process.config import Config
from wmk_process.conform import Model, call, read_model

PAGE = 500
GROUPS = {
    "contested": "Contested (some source asserts, some denies)",
    "denied": "Denied (no source asserts)",
    "one view": "Stated by one view only",
    "agreed": "Agreed by two or more views",
    "unstated": "Stated by none of these views",
}


@dataclass(frozen=True)
class Stance:
    """One collection's latest assertion on an edge."""

    collection: str
    holds: bool
    valid_from: str | None = None
    valid_to: str | None = None
    # The claim of that assertion, and its log offset.
    claim_id: str | None = field(default=None, compare=False)
    offset: int | None = field(default=None, compare=False)

    def text(self) -> str:
        words = "asserts" if self.holds else "denies"
        if self.holds and self.valid_from:
            words += f" from {self.valid_from}"
        if self.holds and self.valid_to:
            words += f" until {self.valid_to}"
        return words

    def to_json(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "holds": self.holds,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


@dataclass(frozen=True)
class Said:
    """The claim behind a stance: its text, and the words of its source it quotes, if any.
    Sealed text is opened for the reader, or reads [erased] once its key is destroyed."""

    claim_id: str
    offset: int
    collection: str
    basis: str
    text: str
    quote: str | None = None

    @classmethod
    def of(cls, entry: dict[str, Any], collection: str) -> Said:
        claim = entry["claim"]
        return cls(
            str(claim["id"]),
            int(entry["offset"]),
            collection,
            str(claim.get("basis", "")),
            str(claim.get("text", "")),
            claim.get("quote"),
        )


@dataclass
class Fact:
    """A step of the process (its `part_of` edge) or a flow out of one."""

    edge_id: str
    kind: str  # step or flow
    label: str
    belief: str | None
    sort: tuple[str, ...]
    views: dict[str, list[Stance]] = field(default_factory=dict)

    def stance(self, view: str) -> str:
        found = self.views.get(view) or []
        if not found:
            return "silent"
        if all(s.holds for s in found):
            return "asserts"
        if not any(s.holds for s in found):
            return "denies"
        return "divided"

    @property
    def group(self) -> str:
        stances = [s for found in self.views.values() for s in found]
        asserted = [v for v, found in self.views.items() if any(s.holds for s in found)]
        denied = any(not s.holds for s in stances)
        if asserted and denied:
            return "contested"
        if denied:
            return "denied"
        if not asserted:
            return "unstated"
        return "one view" if len(asserted) == 1 else "agreed"

    def text(self, order: list[str]) -> str:
        parts = []
        for view in order:
            found = self.views.get(view) or []
            stance = self.stance(view)
            if stance == "silent":
                continue
            if stance == "divided" or len({s.text() for s in found}) > 1:
                detail = ", ".join(f"{s.collection} {s.text()}" for s in found)
                parts.append(f"{view} {stance} ({detail})")
            else:
                parts.append(f"{view} {found[0].text()}")
        said = "; ".join(parts) if parts else "no view states it"
        return f"- {self.label} [{self.belief or 'unknown'}]: {said}."

    def to_json(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "kind": self.kind,
            "fact": self.label,
            "belief": self.belief,
            "group": self.group,
            "views": {
                view: {"stance": self.stance(view), "sources": [s.to_json() for s in found]}
                for view, found in self.views.items()
            },
        }


@dataclass
class Comparison:
    process: str
    offset: int
    views: dict[str, tuple[str, ...]]
    facts: list[Fact]
    # Stances on edges that are not facts (`also` in `read`), by edge id and view.
    others: dict[str, dict[str, list[Stance]]] = field(default_factory=dict)
    # The claim behind each stance, by claim id.
    claims: dict[str, Said] = field(default_factory=dict)

    def sources(self, edge_id: str) -> dict[str, list[Stance]]:
        """Each view's stances on an edge, a fact or one of the others."""
        for f in self.facts:
            if f.edge_id == edge_id:
                return f.views
        return self.others.get(edge_id, {})

    def group(self, name: str) -> list[Fact]:
        return [f for f in self.facts if f.group == name]

    def fact(self, label: str) -> Fact:
        """The fact with this label (a step's name, or "A -> B" with ", when ..." for a branch)."""
        found = [f for f in self.facts if f.label == label]
        if len(found) != 1:
            raise KeyError(label)
        return found[0]

    def text(self) -> str:
        order = list(self.views)
        lines = [f"Views of {self.process}, compared at log offset {self.offset}"]
        lines += [f"- {view}: {', '.join(collections)}" for view, collections in self.views.items()]
        for name, title in GROUPS.items():
            facts = self.group(name)
            if name == "unstated" and not facts:
                continue
            lines += ["", f"{title}:"]
            lines += [f.text(order) for f in facts] or ["- none"]
        return "\n".join(lines) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {
            "process": self.process,
            "offset": self.offset,
            "views": {view: list(collections) for view, collections in self.views.items()},
            "facts": [f.to_json() for f in self.facts],
        }


def facts(model: Model) -> dict[str, Fact]:
    """The model's steps and flows, keyed by edge id, steps first."""
    n = model.nodes
    found: dict[str, Fact] = {}
    for node in sorted((x for x in n.values() if x.part_of), key=lambda x: (x.name, x.id)):
        label = node.name if node.kind == "activity" else f"{node.name} ({node.kind})"
        found[node.part_of or ""] = Fact(node.part_of or "", "step", label, node.belief, ("0", node.name))
    for flow in sorted(model.flows, key=lambda f: (n[f.frm].name, n[f.to].name, f.when or "", f.id)):
        label = f"{n[flow.frm].name} -> {n[flow.to].name}"
        if flow.when is not None:
            label += f", when {flow.when}"
        found[flow.id] = Fact(flow.id, "flow", label, flow.belief, ("1", n[flow.frm].name, n[flow.to].name))
    return found


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


def stances(entries: list[dict[str, Any]], collection: str, edges: set[str]) -> dict[str, Stance]:
    """Each edge's latest assertion in a collection's log entries, oldest entry first."""
    latest: dict[str, Stance] = {}
    for entry in entries:
        for op in entry.get("ops") or []:
            if op.get("op") != "assert" or op.get("target") != "edge" or op.get("edge_id") not in edges:
                continue
            holds = op.get("polarity") in (1, "positive")
            window = _day(op.get("valid_from")), _day(op.get("valid_to"))
            claim = (entry.get("claim") or {}).get("id")
            offset = int(entry["offset"]) if "offset" in entry else None
            latest[op["edge_id"]] = Stance(collection, holds, *window, claim_id=claim, offset=offset)
    return latest


async def entries(client: Any, collection: str, through: int) -> list[dict[str, Any]]:
    """The log entries citing a collection up to an offset, oldest first."""
    found: list[dict[str, Any]] = []
    after = 0
    while True:
        page = await call(
            client,
            "query_log",
            {
                "collection": collection,
                "order": "asc",
                "after_offset": after,
                "before_offset": through + 1,
                "limit": PAGE,
            },
        )
        found += page["entries"]
        if len(page["entries"]) < PAGE:
            return found
        after = int(page["entries"][-1]["offset"])


async def read(client: Any, cfg: Config, model: Model | None = None, also: Iterable[str] = ()) -> Comparison:
    """The views the configuration names, compared on the process the kernel holds now (or on
    `model`, read earlier). `also` names more edges to keep each collection's stance on, such
    as the measures a ranking used."""
    model = model or await read_model(client, cfg)
    found = facts(model)
    others: dict[str, dict[str, list[Stance]]] = {e: {} for e in also if e not in found}
    claims: dict[str, Said] = {}
    views = cfg.compared()
    for view, collections in views.items():
        for collection in collections:
            logged = await entries(client, collection, model.offset)
            at = {int(e["offset"]): e for e in logged}
            for edge_id, stance in stances(logged, collection, set(found) | set(others)).items():
                held = found[edge_id].views if edge_id in found else others[edge_id]
                held.setdefault(view, []).append(stance)
                if stance.claim_id and stance.offset is not None:
                    claims[stance.claim_id] = Said.of(at[stance.offset], collection)
    ordered = sorted(found.values(), key=lambda f: (f.sort, f.label, f.edge_id))
    return Comparison(model.name, model.offset, views, ordered, others, claims)

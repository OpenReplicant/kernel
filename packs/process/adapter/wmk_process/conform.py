"""The mapped process checked against the log (ADR 0027).

`read_model` reads the process as the kernel holds it, through the gateway's
`query_graph`, and records the offset it read at. `build` replays the log's cases against
it and writes a conformance digest. Each line of the digest is one observed verdict on an
existing edge, by `edge_id`. A verdict is an assertion or a denial, so a later run can
overturn an earlier denial: closing a run retracts only an earlier run's assertions.
- A step (an `activity` `part_of` the process): asserted if some case ran it, denied if
  none did.
- A flow between two activities without a condition: asserted if some case followed it,
  denied if none did while the first ran.
- A decision: a gateway (or an activity) whose flows out all carry `when`. A case reaches
  a gateway from a step that flows into it, unless it continues along one of that step's
  other flows (a review that sends the request back for revision never reaches the
  decision on its amount). Every time a case reaches it, the branches whose condition
  holds are expected (the `else` branch when none does), and the step that follows in the
  log is the branch taken. A branch is
  asserted when every case that met its condition took it, and denied when some did not.

Whatever cannot be checked this way is listed in the digest and gets no verdict:
- decisions whose branches lead to anything but activities;
- gateways reached through other gateways, or from steps whose other flows lead to
  gateways or outcomes;
- conditions on attributes the log lacks.

Activity names are matched after the configuration's label map, with the kernel's name
normalisation.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from wmk_adapter.plan import Plan, Source, normalize
from wmk_process import conditions
from wmk_process.config import Config
from wmk_process.discover import EXAMPLES, EXTRACTOR, EXTRACTOR_VERSION
from wmk_process.logs import Log


class ModelError(Exception):
    """The process is not mapped, or the gateway refused a read."""


@dataclass
class Node:
    id: str
    name: str
    kind: str
    part_of: str | None = None  # the edge into the process
    belief: str | None = None  # of that edge


@dataclass
class Flow:
    id: str
    frm: str
    to: str
    when: str | None
    belief: str | None = None


@dataclass
class Model:
    process_id: str
    name: str
    offset: int
    nodes: dict[str, Node] = field(default_factory=dict)
    flows: list[Flow] = field(default_factory=list)

    def fingerprint(self) -> str:
        """The model's structure (steps, flows, conditions), not its belief: checking an
        unchanged model against an unchanged log gives the same digest, which is skipped."""
        parts = [
            f"{n.id} {n.kind} {n.name} {n.part_of}" for n in sorted(self.nodes.values(), key=lambda n: n.id)
        ]
        parts += [f"{f.id} {f.frm} {f.to} {f.when}" for f in self.flows]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]

    def out(self, node_id: str) -> list[Flow]:
        return [f for f in self.flows if f.frm == node_id]

    def into(self, node_id: str) -> list[Flow]:
        return [f for f in self.flows if f.to == node_id]


STEPS = """
MATCH (s:Entity)-[r:part_of]->(p:Entity {id: $process})
RETURN s.id AS id, s.name AS name, s.kind AS kind, r.id AS edge, r.belief_status AS belief
"""
FLOWS = """
MATCH (a:Entity)-[:part_of]->(p:Entity {id: $process}), (a)-[f:flows_to]->(b:Entity)
RETURN a.id AS frm, b.id AS to, b.name AS to_name, b.kind AS to_kind, f.id AS id, f.props AS props,
       f.belief_status AS belief
"""


async def call(client: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    body = json.loads(result.content[0].text)
    if result.is_error:
        raise ModelError(f"{tool}: {body.get('detail')}")
    return body


async def read_model(client: Any, cfg: Config) -> Model:
    """The process named in the configuration, as the kernel holds it now."""
    found = await call(
        client,
        "lookup_entities",
        {"queries": [{"name": cfg.process, "type": "Entity", "kind": "process"}], "limit": 5},
    )
    same = [c for c in found["results"][0]["candidates"] if c["stage"] in ("identity", "normalized")]
    if not same:
        raise ModelError(f"no process named {cfg.process!r} is mapped yet")
    pid = same[0]["node_id"]
    steps = await call(client, "query_graph", {"cypher": STEPS, "params": {"process": pid}, "limit": 1000})
    flows = await call(client, "query_graph", {"cypher": FLOWS, "params": {"process": pid}, "limit": 1000})
    if steps["truncated"] or flows["truncated"]:
        raise ModelError(f"{cfg.process} has more than 1000 steps or flows; not checked")
    model = Model(pid, cfg.process, int(steps["head_offset"]))
    for row in steps["rows"]:
        model.nodes[row["id"]] = Node(row["id"], row["name"], row["kind"], row["edge"], row.get("belief"))
    for row in flows["rows"]:
        model.nodes.setdefault(row["to"], Node(row["to"], row["to_name"], row["to_kind"]))
        when = (row.get("props") or {}).get("when")
        model.flows.append(
            Flow(row["id"], row["frm"], row["to"], str(when) if when is not None else None, row.get("belief"))
        )
    model.flows.sort(key=lambda f: (f.frm, f.to, f.when or "", f.id))
    return model


@dataclass
class Branch:
    flow: Flow
    condition: conditions.Node
    met: int = 0
    took: int = 0
    elsewhere: Counter[str] = field(default_factory=Counter)
    examples: set[str] = field(default_factory=set)


def build(cfg: Config, log: Log, model: Model) -> Plan:
    plan = Plan(EXTRACTOR, EXTRACTOR_VERSION)
    first, last = log.window()
    traces = {cid: [cfg.step(e.activity) for e in case.events] for cid, case in log.cases.items()}
    runs: Counter[str] = Counter()
    follows: Counter[tuple[str, str]] = Counter()
    for steps in traces.values():
        runs.update(normalize(s) for s in steps)
        follows.update((normalize(a), normalize(b)) for a, b in pairwise(steps))
    n = model.nodes
    activity = {k for k, v in n.items() if v.kind == "activity"}

    lines: list[str] = []
    skipped: list[str] = []

    span = f"between {first.date().isoformat()} and {last.date().isoformat()}"
    where = f"in the event log {log.name} ({len(log.cases)} cases {span})"

    # Steps ------------------------------------------------------------------------------
    step_lines: list[tuple[str, str, bool, str]] = []
    for node in sorted((n[k] for k in activity if n[k].part_of), key=lambda x: (x.name, x.id)):
        times = runs[normalize(node.name)]
        if times:
            step_lines.append(
                (
                    f"- {node.name}: ran {times} times.",
                    node.part_of or "",
                    True,
                    f"{node.name}, a step of {model.name}, ran {times} times {where}.",
                )
            )
        else:
            step_lines.append(
                (
                    f"- {node.name}: never ran.",
                    node.part_of or "",
                    False,
                    f"No case of {model.name} ran {node.name} {where}.",
                )
            )

    # Flows between activities -------------------------------------------------------------
    flow_lines: list[tuple[str, str, bool, str]] = []
    for flow in model.flows:
        a, b = n[flow.frm].name, n[flow.to].name
        if {n[flow.frm].kind, n[flow.to].kind} & {"trigger", "outcome"}:
            skipped.append(f"- {a} -> {b}: triggers and outcomes are not in the log.")
        if flow.when is not None or flow.frm not in activity or flow.to not in activity:
            continue
        ran = runs[normalize(a)]
        if not ran:
            skipped.append(f"- {a} -> {b}: {a} never ran.")
            continue
        times = follows[(normalize(a), normalize(b))]
        if times:
            flow_lines.append(
                (
                    f"- {a} -> {b}: followed {times} times.",
                    flow.id,
                    True,
                    f"In {model.name}, {a} was directly followed by {b} {times} times {where}.",
                )
            )
        else:
            flow_lines.append(
                (
                    f"- {a} -> {b}: never followed, though {a} ran {ran} times.",
                    flow.id,
                    False,
                    f"In {model.name}, {a} was never directly followed by {b}, though {a} ran {ran} times "
                    f"{where}.",
                )
            )

    # Decisions ----------------------------------------------------------------------------
    decision_lines: list[tuple[str, str, bool, str]] = []
    deciders = sorted({f.frm for f in model.flows if f.when is not None}, key=lambda k: (n[k].name, k))
    for d in deciders:
        outs = model.out(d)
        label = f'{n[d].kind} "{n[d].name}"'
        if any(f.when is None for f in outs):
            skipped.append(f"- {label}: some flows out have a condition and some do not.")
            continue
        if any(f.to not in activity for f in outs):
            skipped.append(f"- {label}: a branch leads to something other than an activity.")
            continue
        # The steps a case reaches the decision from, each with the steps its other flows lead
        # to: a case that continues along one of those never reached the decision. A step the
        # decision branches to is not one of them, even when a flow leads there directly (the
        # log, which never sees gateways, maps one).
        if d in activity:
            entries: dict[str, set[str]] = {d: set()}
        else:
            ins = model.into(d)
            targets = {f.to for f in outs}
            entries = {f.frm: {o.to for o in model.out(f.frm) if o.to != d} - targets for f in ins}
            if not ins or any(e not in activity or not others <= activity for e, others in entries.items()):
                skipped.append(
                    f"- {label}: reached through other gateways, or from steps whose other flows "
                    "lead to gateways or outcomes."
                )
                continue
        try:
            branches = [Branch(f, conditions.parse(f.when or "")) for f in outs]
        except conditions.ConditionError as exc:
            skipped.append(f"- {label}: {exc}")
            continue
        needed = set().union(*(conditions.attributes(b.condition) for b in branches))
        lacking = sorted(a for a in needed if not any(a in c.attrs for c in log.cases.values()))
        if lacking:
            skipped.append(f"- {label}: the log has no case attribute {', '.join(lacking)}.")
            continue
        bypass = {
            normalize(n[e].name): {normalize(n[o].name) for o in others} for e, others in entries.items()
        }
        missing: set[str] = set()
        for cid, steps in traces.items():
            attrs = log.cases[cid].attrs
            for k in range(len(steps) - 1):
                here = normalize(steps[k])
                if here not in bypass or normalize(steps[k + 1]) in bypass[here]:
                    continue
                met: list[Branch] = []
                try:
                    met = [b for b in branches if conditions.holds(b.condition, attrs)]
                except conditions.MissingAttribute as exc:
                    missing.add(str(exc.args[0]))
                    continue
                if not met:
                    met = [b for b in branches if b.condition == ("else",)]
                taken = normalize(steps[k + 1])
                for b in met:
                    b.met += 1
                    if normalize(n[b.flow.to].name) == taken:
                        b.took += 1
                    else:
                        b.elsewhere[steps[k + 1]] += 1
                        b.examples.add(cid)
        after = " or ".join(sorted(n[e].name for e in entries))
        for b in sorted(branches, key=lambda b: (b.flow.when or "", n[b.flow.to].name)):
            target = n[b.flow.to].name
            head = f"After {after}, when {b.flow.when}, to {target}"
            if not b.met:
                skipped.append(f"- {head}: the condition never held.")
                continue
            if not b.elsewhere:
                decision_lines.append(
                    (
                        f"- {head}: held {b.met} times; all continued there.",
                        b.flow.id,
                        True,
                        f"In {model.name}, every one of the {b.met} times '{b.flow.when}' held after "
                        f"{after}, the case continued at {target} {where}.",
                    )
                )
                continue
            went = ", ".join(
                f"{s} {c}" for s, c in sorted(b.elsewhere.items(), key=lambda kv: (-kv[1], kv[0]))
            )
            cases = ", ".join(sorted(b.examples)[:EXAMPLES])
            off = b.met - b.took
            decision_lines.append(
                (
                    f"- {head}: held {b.met} times; {b.took} continued there, {off} elsewhere "
                    f"({went}; cases {cases}).",
                    b.flow.id,
                    False,
                    f"In {model.name}, {off} of the {b.met} times '{b.flow.when}' held after {after}, the "
                    f"case did not continue at {target} but at {went} ({cases}) {where}.",
                )
            )
        if missing:
            skipped.append(
                f"- {label}: some cases lack {', '.join(sorted(missing))}; those were not counted."
            )

    # The digest, one line per verdict: (offset of its line, edge id, holds, claim text) ----------
    verdicts: list[tuple[int, str, bool, str]] = []

    def line(text: str) -> int:
        offset = sum(len(x) + 1 for x in lines)
        lines.append(text)
        return offset

    line(f"Conformance of {model.name} to the event log {log.name}")
    line(f"Log: {log.name} ({log.format}, sha256 {log.sha256}), {len(log.cases)} cases {span}")
    line(
        f"Model: process {model.process_id}, {len(n)} nodes and {len(model.flows)} flows "
        f"(fingerprint {model.fingerprint()})"
    )
    for title, group in (("Steps:", step_lines), ("Flows:", flow_lines), ("Decisions:", decision_lines)):
        line("")
        line(title)
        if not group:
            line("- none checked")
        for text, edge_id, holds, claim in group:
            verdicts.append((line(text), edge_id, holds, claim))
    line("")
    line("Not checked:")
    for text in skipped or ["- nothing"]:
        line(text)
    content = "\n".join(lines) + "\n"

    alias = f"{log.name}.conformance"
    plan.source(
        Source(
            alias=alias,
            content=content,
            title=f"Conformance of {model.name} to {log.name}",
            uri=f"conformance:{log.name}",
            collection=cfg.conformance_collection,
            origins=cfg.origins,
            metadata={
                "log": log.name,
                "sha256": log.sha256,
                "process": model.process_id,
                "model": model.fingerprint(),
                "read_at_offset": model.offset,
            },
        )
    )
    for offset, edge_id, holds, claim in verdicts:
        fact = plan.fact(claim, source=alias, at=offset)
        plan.on_edge(fact, edge_id, deny=not holds)
    return plan

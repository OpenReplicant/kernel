"""What to automate first in a mapped process (ADR 0033).

`read` reads, through the gateway and at one offset, what the kernel holds about the
process: its steps and flows with their belief (as `conform` reads them), the KPIs that
measure each step (by the names in `discover.KPIS`), and the systems each step is
performed in. It writes nothing. Each step with executions measured gets six factors from
0 to 1:
- volume: its executions over the most executions of any step;
- waiting: the total time before it over the largest such total;
- rework: its repeats over its executions;
- handoffs: its executions after another role's step over its executions;
- rule: 1 when a routing rule into it (a flow with `when`) is contested or rejected: some
  source states the rule and some source denies it;
- system: 1 when it is performed in a system.

Its score is the sum of the factors, each times its weight (`rank.weights` in the
configuration, 1 by default). Each step also lists what to settle first: the contested
facts about it (its place in the process and its flows). They do not change the score.

Steps with no executions measured are listed apart, and so are the decisions already
written as rules: steps whose flows out all carry conditions, which a runtime can run as
they are. Where a KPI has several values, the latest period's is used, and a retracted one
never is.

The scores say where time goes and where rules break. Whether a step can be automated is
for the people who run it to judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wmk_adapter.plan import normalize
from wmk_process.compare import facts
from wmk_process.config import FACTORS, Config
from wmk_process.conform import Model, ModelError, Node, call, read_model
from wmk_process.discover import KPIS

LIMIT = 1000
MEASURES = """
MATCH (k:Entity)-[m:monitors]->(s:Entity)-[:part_of]->(p:Entity {id: $process})
WHERE m.kind = 'measures'
RETURN k.name AS kpi, s.id AS step, m.id AS edge, m.props AS props, m.valid_from AS valid_from,
       m.valid_to AS valid_to, m.belief_status AS belief
"""
SYSTEMS = """
MATCH (s:Entity)-[:part_of]->(p:Entity {id: $process}), (s)-[d:depends_on]->(c:Entity)
WHERE d.kind = 'performed_in'
RETURN s.id AS step, c.name AS system, d.id AS edge, d.belief_status AS belief
"""
CLOSING = (
    "Scores show where time goes and where rules break. Whether a step can be automated is for the "
    "people who run it to judge."
)


@dataclass(frozen=True)
class Measure:
    """A KPI's value for a step, from the `measures` edge used."""

    value: float
    unit: str
    edge_id: str
    belief: str | None
    valid_from: str | None
    valid_to: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "edge_id": self.edge_id,
            "belief": self.belief,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


@dataclass(frozen=True)
class Ref:
    """A fact the ranking rests on: an edge, its label and its belief."""

    edge_id: str
    label: str
    belief: str | None

    def text(self) -> str:
        return f"{self.label} [{self.belief or 'unknown'}]"

    def to_json(self) -> dict[str, Any]:
        return {"edge_id": self.edge_id, "fact": self.label, "belief": self.belief}


@dataclass
class Step:
    node: Node
    measures: dict[str, Measure] = field(default_factory=dict)
    systems: list[Ref] = field(default_factory=list)
    rules: list[Ref] = field(default_factory=list)
    unsettled: list[Ref] = field(default_factory=list)
    points: dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    def value(self, metric: str) -> float:
        found = self.measures.get(metric)
        return found.value if found else 0.0

    def detail(self, factor: str) -> str:
        """The inputs behind a factor, in words."""
        m = self.measures
        if factor == "volume":
            return f"{self.value('executions'):g} executions"
        if factor == "waiting":
            if "total_time" not in m:
                return "not measured (no step before it, or no times)"
            median = f", median {m['median_time'].value:.1f}" if "median_time" in m else ""
            return f"{m['total_time'].value:.1f} hours in total since the case's previous event{median}"
        if factor == "rework":
            return f"{self.value('repeats'):g} repeats"
        if factor == "handoffs":
            if "handoffs" not in m:
                return "not measured (no step before it, or no roles)"
            return f"{m['handoffs'].value:g} after another role's step"
        if factor == "rule":
            return "; ".join(r.text() for r in self.rules) or "no routing rule into it is denied"
        return ", ".join(r.label for r in self.systems) or "not performed in a system"

    def to_json(self, rank: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "step": self.node.name,
            "node_id": self.node.id,
            "part_of": self.node.part_of,
        }
        if rank is not None:
            out = {
                "rank": rank,
                **out,
                "score": self.score,
                "factors": {f: round(self.points[f], 4) for f in FACTORS},
                "measures": {k: v.to_json() for k, v in sorted(self.measures.items())},
                "systems": [r.to_json() for r in self.systems],
                "rules": [r.to_json() for r in self.rules],
            }
        return {**out, "settle_first": [r.to_json() for r in self.unsettled]}


@dataclass
class Decision:
    """A step whose flows out all carry conditions: a rule a runtime can run as it is."""

    node: Node
    branches: list[Ref]

    @property
    def label(self) -> str:
        return self.node.name if self.node.kind == "activity" else f"{self.node.name} ({self.node.kind})"

    def to_json(self) -> dict[str, Any]:
        return {
            "decision": self.label,
            "node_id": self.node.id,
            "branches": [b.to_json() for b in self.branches],
        }


@dataclass
class Ranking:
    process: str
    offset: int
    weights: dict[str, float]
    steps: list[Step]
    unmeasured: list[Step]
    decisions: list[Decision]

    def step(self, name: str) -> Step:
        found = [s for s in self.steps + self.unmeasured if s.node.name == name]
        if len(found) != 1:
            raise KeyError(name)
        return found[0]

    def window(self) -> tuple[str | None, str | None]:
        """The period the executions measured cover: the earliest start to the latest end."""
        used = [s.measures["executions"] for s in self.steps]
        starts = [m.valid_from for m in used if m.valid_from]
        ends = [m.valid_to for m in used if m.valid_to]
        return (min(starts)[:10] if starts else None, max(ends)[:10] if ends else None)

    def text(self) -> str:
        weights = ", ".join(f"{f} {self.weights[f]:g}" for f in FACTORS)
        lines = [
            f"What to automate in {self.process}, ranked at log offset {self.offset}",
            f"Weights: {weights} (a score of at most {sum(self.weights.values()):g})",
        ]
        start, end = self.window()
        if start or end:
            lines.append(f"Measured from {start or 'the start'} until {end or 'now'}")
        for n, s in enumerate(self.steps, start=1):
            lines += ["", f"{n}. {s.node.name}: {s.score:.2f}"]
            lines += [f"   {f:<9} {s.points[f]:.2f}  {s.detail(f)}" for f in FACTORS]
            if s.unsettled:
                lines.append("   settle first: " + "; ".join(r.text() for r in s.unsettled))
        if not self.steps:
            lines += ["", "No step has executions measured: map an event log first (wmk-process discover)."]
        if self.unmeasured:
            lines += ["", "Not measured (no executions in the kernel):"]
            for s in self.unmeasured:
                settle = "; settle first: " + "; ".join(r.text() for r in s.unsettled) if s.unsettled else ""
                lines.append(f"- {s.node.name}{settle}")
        if self.decisions:
            lines += ["", "Decisions written as rules (a runtime can run them as they are):"]
            for d in self.decisions:
                lines.append(f"- {d.label}: " + "; ".join(b.text() for b in d.branches))
        lines += ["", CLOSING]
        return "\n".join(lines) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {
            "process": self.process,
            "offset": self.offset,
            "weights": dict(self.weights),
            "steps": [s.to_json(n) for n, s in enumerate(self.steps, start=1)],
            "not_measured": [s.to_json() for s in self.unmeasured],
            "decisions": [d.to_json() for d in self.decisions],
        }


def pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The value to use among a KPI's edges: never a retracted one; the latest period's
    first, then an accepted one, then by edge id."""
    live = [r for r in rows if r.get("belief") != "rejected"]
    if not live:
        return None
    return max(
        live,
        key=lambda r: (
            r.get("valid_to") or "9999",
            r.get("valid_from") or "",
            r.get("belief") == "accepted",
            r["edge"],
        ),
    )


def measure(rows: list[dict[str, Any]]) -> Measure | None:
    """The value to use among a KPI's edges (see `pick`), if it has a number."""
    row = pick(rows)
    props = row.get("props") or {} if row else {}
    if not row or not isinstance(props.get("value"), int | float):
        return None
    return Measure(
        float(props["value"]),
        str(props.get("unit", "")),
        row["edge"],
        row.get("belief"),
        row.get("valid_from"),
        row.get("valid_to"),
    )


def score(steps: list[Step], weights: dict[str, float]) -> None:
    """Each step's factors and score, relative to the other steps of the process."""
    most = max((s.value("executions") for s in steps), default=0.0)
    longest = max((s.value("total_time") for s in steps), default=0.0)
    for s in steps:
        runs = s.value("executions")
        s.points = {
            "volume": runs / most if most else 0.0,
            "waiting": s.value("total_time") / longest if longest else 0.0,
            "rework": s.value("repeats") / runs if runs else 0.0,
            "handoffs": s.value("handoffs") / runs if runs else 0.0,
            "rule": 1.0 if s.rules else 0.0,
            "system": 1.0 if s.systems else 0.0,
        }
        s.score = round(sum(weights[f] * s.points[f] for f in FACTORS), 2)


def build(
    model: Model, weights: dict[str, float], measures: list[dict[str, Any]], systems: list[dict[str, Any]]
) -> Ranking:
    """The ranking from what was read: the model, its KPI rows and its system rows."""
    n = model.nodes
    labels = facts(model)

    def ref(edge_id: str) -> Ref:
        fact = labels[edge_id]
        return Ref(edge_id, fact.label, fact.belief)

    steps: dict[str, Step] = {
        k: Step(node)
        for k, node in n.items()
        if node.kind == "activity" and node.part_of and node.belief != "rejected"
    }
    for k, s in steps.items():
        names = {normalize(t.format(step=s.node.name)): metric for metric, t in KPIS.items()}
        found: dict[str, list[dict[str, Any]]] = {}
        for row in measures:
            metric = names.get(normalize(str(row["kpi"]))) if row["step"] == k else None
            if metric:
                found.setdefault(metric, []).append(row)
        for metric, rows in found.items():
            if used := measure(rows):
                s.measures[metric] = used
        s.systems = sorted(
            (
                Ref(r["edge"], str(r["system"]), r.get("belief"))
                for r in systems
                if r["step"] == k and r.get("belief") != "rejected"
            ),
            key=lambda r: (r.label, r.edge_id),
        )
        touching = [f for f in model.flows if k in (f.frm, f.to)]
        s.rules = sorted(
            (
                ref(f.id)
                for f in touching
                if f.to == k and f.when is not None and f.belief in ("contested", "rejected")
            ),
            key=lambda r: (r.label, r.edge_id),
        )
        unsettled = [ref(s.node.part_of or "")] + [ref(f.id) for f in touching]
        s.unsettled = sorted(
            (r for r in unsettled if r.belief == "contested"), key=lambda r: (r.label, r.edge_id)
        )

    measured = [s for s in steps.values() if s.value("executions") > 0]
    score(measured, weights)
    measured.sort(key=lambda s: (-s.score, s.node.name, s.node.id))
    unmeasured = sorted(
        (s for s in steps.values() if s.value("executions") <= 0), key=lambda s: (s.node.name, s.node.id)
    )

    decisions = []
    for k in sorted({f.frm for f in model.flows}, key=lambda k: (n[k].name, k)):
        outs = model.out(k)
        if all(f.when is not None for f in outs):
            branches = [
                Ref(f.id, f"when {f.when}, to {n[f.to].name}", f.belief)
                for f in sorted(outs, key=lambda f: (f.when == "else", f.when or "", n[f.to].name, f.id))
            ]
            decisions.append(Decision(n[k], branches))
    return Ranking(model.name, model.offset, dict(weights), measured, unmeasured, decisions)


async def rows(client: Any, cypher: str, model: Model) -> list[dict[str, Any]]:
    found = await call(
        client,
        "query_graph",
        {
            "cypher": cypher,
            "params": {"process": model.process_id},
            "known_at_offset": model.offset,
            "limit": LIMIT,
        },
    )
    if found["truncated"]:
        raise ModelError(f"{model.name} has more than {LIMIT} measures or systems; not ranked")
    return list(found["rows"])


async def read(client: Any, cfg: Config, model: Model | None = None) -> Ranking:
    """The steps of the process the configuration names, ranked on what the kernel holds now
    (or at the offset of `model`, read earlier)."""
    model = model or await read_model(client, cfg)
    return build(model, cfg.weights, await rows(client, MEASURES, model), await rows(client, SYSTEMS, model))

"""The discovery report (ADR 0034): the process as mapped, where its views disagree, what the
log measures and what to automate first, each sentence with the claims it rests on.

`read` reads the process once, through the gateway and at one offset: the ranking (ADR
0033), the process's own KPIs, then the views compared (ADR 0032) on the process's steps and
flows and on every edge the ranking used. It writes nothing.

A sentence about a fact cites, for each collection of the views read, the claim of that
collection's latest assertion on the fact's edge: the assertion belief counts. Sentences
that state no fact (the views read, the weights, a step with nothing measured, the closing
note) cite nothing. The text is Markdown, with the citations as footnotes numbered in order
of first use. Each footnote gives the view, the collection, the claim's basis and log offset,
and the words of the source the claim quotes; an observed claim, which quotes nothing, gives
its own text. Claims are attributed to their collections, never to the people who wrote
them. Sealed words are opened for the reader, and read [erased] once their key is destroyed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from wmk_adapter.plan import normalize
from wmk_process import compare, rank
from wmk_process.compare import Comparison, Fact, Said, Stance
from wmk_process.config import FACTORS, Config
from wmk_process.conform import Model, read_model
from wmk_process.discover import PROCESS_KPIS

PROCESS_MEASURES = """
MATCH (k:Entity)-[m:monitors]->(p:Entity {id: $process})
WHERE m.kind = 'measures'
RETURN k.name AS kpi, m.id AS edge, m.props AS props, m.valid_from AS valid_from, m.valid_to AS valid_to,
       m.belief_status AS belief
"""
INTRO = (
    "Each sentence ends with the claims it rests on, as footnotes that give where each claim comes "
    "from and the words of its source. In brackets is the kernel's belief in a fact: accepted, "
    "contested (some source states it and some denies it) or rejected."
)
GROUPS = {
    "contested": "Contested: some source states it, some denies it",
    "denied": "Denied: some source denies it, none states it",
    "one view": "Stated by one view only",
}
VERBS = {
    "asserts": ("states it", "state it"),
    "denies": ("denies it", "deny it"),
    "divided": ("is divided", "are divided"),
}
FACTORS_TEXT = (
    "Each step's score is the sum of six factors from 0 to 1, each times its weight: volume (its "
    "executions over the most of any step), waiting (the time before it over the longest), rework "
    "(repeats over executions), handoffs (executions after another role's step over executions), "
    "rule (1 when a routing rule into it is contested or rejected) and system (1 when it is performed "
    "in a system)."
)


@dataclass
class Line:
    """One sentence of the report, the edges it is about and the claims it cites."""

    text: str
    edges: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    depth: int = 0  # 1: under the line before it
    number: int | None = None  # its place in a numbered list
    bullet: bool = True  # False: a paragraph

    def to_json(self, refs: dict[str, int]) -> dict[str, Any]:
        return {
            "text": self.text,
            "edges": list(self.edges),
            "claims": list(self.claims),
            "refs": sorted(refs[c] for c in self.claims),
        }


@dataclass
class Section:
    title: str
    lines: list[Line]
    level: int = 2


@dataclass
class Report:
    process: str
    offset: int
    views: dict[str, tuple[str, ...]]
    sections: list[Section]
    claims: dict[str, Said]

    def refs(self) -> dict[str, int]:
        """Each claim cited, by footnote number in order of first use."""
        found: dict[str, int] = {}
        for section in self.sections:
            for line in section.lines:
                for claim in line.claims:
                    found.setdefault(claim, len(found) + 1)
        return found

    def section(self, title: str) -> Section:
        return next(s for s in self.sections if s.title == title)

    def view(self, collection: str) -> str:
        return next((v for v, found in self.views.items() if collection in found), "")

    def source(self, said: Said) -> str:
        """A footnote: where the claim comes from, and its source's words or its own text."""
        words = f"“{said.quote}”" if said.quote else said.text
        where = f"{self.view(said.collection)}, {said.collection}, {said.basis} at log offset {said.offset}"
        return f"{where}: {' '.join(words.split())}"

    def text(self) -> str:
        refs = self.refs()
        out = [
            f"# Discovery report: {self.process}",
            "",
            f"Read from the World Model Kernel at log offset {self.offset}, from these views:",
        ]
        out += [f"- {view}: {', '.join(found)}" for view, found in self.views.items()]
        out += ["", INTRO]
        for section in self.sections:
            out += ["", f"{'#' * section.level} {section.title}", ""]
            width = 2
            for line in section.lines:
                cited = "".join(f"[^{n}]" for n in sorted(refs[c] for c in line.claims))
                body = f"{line.text} {cited}" if cited else line.text
                if not line.bullet:
                    if out[-1]:
                        out.append("")
                    out += [body, ""]
                elif line.depth == 0:
                    prefix = f"{line.number}. " if line.number is not None else "- "
                    width = len(prefix)
                    out.append(prefix + body)
                else:
                    out.append(" " * width + "- " + body)
            if not out[-1]:
                out.pop()
        out += [""] + [f"[^{n}]: {self.source(self.claims[c])}" for c, n in refs.items()]
        return "\n".join(out) + "\n"

    def to_json(self) -> dict[str, Any]:
        refs = self.refs()
        return {
            "process": self.process,
            "offset": self.offset,
            "views": {view: list(found) for view, found in self.views.items()},
            "sections": [
                {
                    "title": s.title,
                    "level": s.level,
                    "sentences": [line.to_json(refs) for line in s.lines],
                }
                for s in self.sections
            ],
            "sources": [
                {
                    "ref": n,
                    "claim_id": c,
                    "view": self.view(self.claims[c].collection),
                    "collection": self.claims[c].collection,
                    "basis": self.claims[c].basis,
                    "offset": self.claims[c].offset,
                    "quote": self.claims[c].quote,
                    "text": self.claims[c].text,
                }
                for c, n in refs.items()
            ],
        }


def flow_order(model: Model) -> list[str]:
    """The process's steps in flow order: breadth first from the steps no flow leads to, along
    the flows out of each by the name of the step they lead to; steps not reached follow by
    name."""
    n = model.nodes
    steps = sorted((k for k, v in n.items() if v.part_of), key=lambda k: (n[k].name, k))
    entered = {f.to for f in model.flows if f.to != f.frm}
    seen: list[str] = []
    for root in [k for k in steps if k not in entered] + steps:
        queue = [root]
        while queue:
            k = queue.pop(0)
            if k in seen:
                continue
            seen.append(k)
            outs = sorted(model.out(k), key=lambda f: (n[f.to].name, f.to))
            queue += [f.to for f in outs if n[f.to].part_of]
    return seen


def belief(fact: Fact) -> str:
    return fact.belief or "unknown"


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary(fact: Fact, order: list[str]) -> str:
    """Which views state the fact, deny it, or are divided on it."""
    by: dict[str, list[str]] = {}
    for view in order:
        if (stance := fact.stance(view)) != "silent":
            by.setdefault(stance, []).append(view)
    if not by:
        return "no view read states it"
    return "; ".join(f"{listed(views)} {VERBS[s][len(views) > 1]}" for s, views in by.items())


def stance(st: Stance) -> str:
    if not st.holds:
        return "denies it"
    words = "states it"
    if st.valid_from:
        words += f" from {st.valid_from}"
    if st.valid_to:
        words += f" until {st.valid_to}"
    return words


def process_measures(name: str, rows: list[dict[str, Any]]) -> dict[str, rank.Measure]:
    """The process's own KPIs (`discover.PROCESS_KPIS`), the value to use of each."""
    found: dict[str, rank.Measure] = {}
    for metric, template in PROCESS_KPIS.items():
        want = normalize(template.format(process=name))
        if used := rank.measure([r for r in rows if normalize(str(r["kpi"])) == want]):
            found[metric] = used
    return found


def measured(step: rank.Step) -> str:
    """A step's measures in words, as `discover` states them."""
    m = step.measures
    parts = [f"{step.node.name} ran {step.value('executions'):g} times"]
    if "repeats" in m:
        repeats = m["repeats"].value
        parts[0] += f", {repeats:g} of them repeats" if repeats else ", none of them repeats"
    if "total_time" in m:
        median = f"a median of {m['median_time'].value:.1f} hours after" if "median_time" in m else "after"
        total = m["total_time"].value
        parts.append(f"it came {median} the case's previous event, {total:.1f} hours in total")
    if "handoffs" in m:
        handoffs = m["handoffs"].value
        parts.append(
            f"{handoffs:g} times it followed another role's step"
            if handoffs
            else "it never followed another role's step"
        )
    return "; ".join(parts) + "."


def build(
    model: Model, comparison: Comparison, ranking: rank.Ranking, measures: dict[str, rank.Measure]
) -> Report:
    """The report from what was read: the model, the views compared on it, its ranking and the
    process's own KPIs."""
    n = model.nodes
    facts = {f.edge_id: f for f in comparison.facts}
    order = list(comparison.views)

    def cited(edges: Iterable[str]) -> tuple[str, ...]:
        found: list[str] = []
        for edge in edges:
            held = comparison.sources(edge)
            for view in order:
                for st in held.get(view, []):
                    if st.claim_id and st.claim_id not in found:
                        found.append(st.claim_id)
        return tuple(found)

    def line(text: str, edges: Iterable[str] = (), **kw: Any) -> Line:
        edges = tuple(edges)
        return Line(text, edges, cited(edges), **kw)

    # The process as mapped: each step, then the flows out of it. -------------------------------
    steps = flow_order(model)
    intro = "Each step, then the flows out of it, with the views that state or deny each."
    mapped = [Line(intro, bullet=False)]
    for k in steps:
        fact = facts[n[k].part_of or ""]
        mapped.append(line(f"{fact.label} [{belief(fact)}]: {summary(fact, order)}.", [fact.edge_id]))
        for flow in sorted(model.out(k), key=lambda f: (n[f.to].name, f.when or "", f.id)):
            to = f"To {n[flow.to].name}" if flow.when is None else f"When {flow.when}, to {n[flow.to].name}"
            ff = facts[flow.id]
            mapped.append(line(f"{to} [{belief(ff)}]: {summary(ff, order)}.", [flow.id], depth=1))
    sections = [Section("The process as mapped", mapped)]

    # Where the views disagree: every source's stance on each fact. ----------------------------
    sections.append(
        Section(
            "Where the views disagree",
            [Line("Each fact, then each source's stance on it.", bullet=False)],
        )
    )
    for group, title in GROUPS.items():
        lines: list[Line] = []
        for fact in comparison.group(group):
            head = f"{fact.label}, a step of {comparison.process}" if fact.kind == "step" else fact.label
            lines.append(Line(f"{head} [{belief(fact)}]"))
            for view in order:
                for st in fact.views.get(view, []):
                    said = (st.claim_id,) if st.claim_id else ()
                    text = f"{view[:1].upper()}{view[1:]}, {st.collection}, {stance(st)}."
                    lines.append(Line(text, (fact.edge_id,), said, depth=1))
        sections.append(Section(title, lines or [Line("None.", bullet=False)], 3))

    # Measures: the process's, then each step's, in flow order. -------------------------------
    lines = []
    if measures:
        first = next(iter(measures.values()))
        when = f"From {(first.valid_from or '')[:10]} until {(first.valid_to or '')[:10]}, "
        said = []
        if "cases" in measures:
            said.append(f"{comparison.process} had {measures['cases'].value:g} cases")
        if "cycle_time" in measures:
            said.append(
                f"the median cycle time, from a case's first event to its last, was "
                f"{measures['cycle_time'].value:.1f} days"
            )
        edges = [m.edge_id for m in measures.values()]
        lines.append(line(when + ", and ".join(said) + ".", edges, bullet=False))
    ranked = {s.node.id: s for s in ranking.steps}
    unmeasured = {s.node.id: s for s in ranking.unmeasured}
    for k in steps:
        if s := ranked.get(k):
            lines.append(line(measured(s), [m.edge_id for m in s.measures.values()]))
        elif k in unmeasured:
            lines.append(Line(f"No executions of {n[k].name} are measured."))
    sections.append(Section("Measures", lines or [Line("Nothing is measured yet.", bullet=False)]))

    # What to automate first: the ranking. ---------------------------------------------------
    weights = ", ".join(f"{f} {ranking.weights[f]:g}" for f in FACTORS)
    lines = [
        Line(FACTORS_TEXT, bullet=False),
        Line(f"Weights: {weights}, for a score of at most {sum(ranking.weights.values()):g}.", bullet=False),
    ]
    for number, s in enumerate(ranking.steps, start=1):
        factors = ", ".join(f"{f} {s.points[f]:.2f}" for f in FACTORS)
        used = [m.edge_id for m in s.measures.values()] + [r.edge_id for r in s.systems + s.rules]
        lines.append(line(f"{s.node.name}: {s.score:.2f} ({factors}).", used, number=number))
        if s.unsettled:
            settle = "; ".join(r.text() for r in s.unsettled)
            lines.append(line(f"Settle first: {settle}.", [r.edge_id for r in s.unsettled], depth=1))
    for s in ranking.unmeasured:
        lines.append(Line(f"Not ranked: {s.node.name}, with no executions measured."))
        if s.unsettled:
            settle = "; ".join(r.text() for r in s.unsettled)
            lines.append(line(f"Settle first: {settle}.", [r.edge_id for r in s.unsettled], depth=1))
    for d in ranking.decisions:
        rules = "; ".join(b.text() for b in d.branches)
        text = f"{d.label} is a decision written as rules, which a runtime can run as they are: {rules}."
        lines.append(line(text, [b.edge_id for b in d.branches]))
    lines.append(Line(rank.CLOSING, bullet=False))
    sections.append(Section("What to automate first", lines))

    return Report(comparison.process, comparison.offset, dict(comparison.views), sections, comparison.claims)


async def read(client: Any, cfg: Config) -> Report:
    """The discovery report on the process the configuration names, as the kernel holds it now."""
    model = await read_model(client, cfg)
    ranking = await rank.read(client, cfg, model)
    measures = process_measures(model.name, await rank.rows(client, PROCESS_MEASURES, model))
    every = ranking.steps + ranking.unmeasured
    used = [m.edge_id for s in every for m in s.measures.values()]
    used += [r.edge_id for s in every for r in s.systems] + [m.edge_id for m in measures.values()]
    comparison = await compare.read(client, cfg, model, also=used)
    return build(model, comparison, ranking, measures)

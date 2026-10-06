"""The view as done (ADR 0027): an event log mapped without reading the model.

The adapter writes a digest of the log, a deterministic text with one line per fact, as
the source its claims cite. The digest shows activities, directly-follows counts with
example cases, roles and measures, and holds no personal data. Each line becomes one
observed claim:
- the process: its case object and KPIs (cases, median cycle time);
- each step: `part_of` the process, the roles responsible for it, the system it is done
  in, and how often it ran;
- each directly-follows pair seen at least `min_count` times: `flows_to`.

Counts stay in the claim text, never in edge props, so a newer export reasserts the same
edges. Each export is mapped in an extraction run in the log's collection, so closing the
run retracts the flows and steps it no longer shows. KPI values are props of their
`measures` edge over the period measured, so a new value is a new edge and the old one is
retracted.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import pairwise

from wmk_adapter.plan import Fact, Plan, Source
from wmk_process.config import Config
from wmk_process.logs import Log

EXTRACTOR = "wmk-process"
EXTRACTOR_VERSION = "0.1.0"
NS = "process"
EXAMPLES = 3


@dataclass
class Stats:
    """What the log shows, by step name (labels mapped through the configuration)."""

    executions: Counter[str] = field(default_factory=Counter)
    cases: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    labels: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    roles: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    follows: Counter[tuple[str, str]] = field(default_factory=Counter)
    follow_cases: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    starts: Counter[str] = field(default_factory=Counter)
    ends: Counter[str] = field(default_factory=Counter)
    cycle_days: list[float] = field(default_factory=list)


def stats(log: Log, cfg: Config) -> Stats:
    st = Stats()
    for case in log.cases.values():
        steps = [cfg.step(e.activity) for e in case.events]
        for event, step in zip(case.events, steps, strict=True):
            st.executions[step] += 1
            st.cases[step].add(case.id)
            st.labels[step].add(event.activity)
            if event.role:
                st.roles[step][event.role] += 1
        for a, b in pairwise(steps):
            st.follows[(a, b)] += 1
            st.follow_cases[(a, b)].add(case.id)
        st.starts[steps[0]] += 1
        st.ends[steps[-1]] += 1
        span = case.events[-1].time - case.events[0].time
        st.cycle_days.append(span.total_seconds() / 86400)
    return st


def examples(ids: set[str]) -> str:
    return ", ".join(sorted(ids)[:EXAMPLES])


def ordered(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def period(log: Log) -> tuple[str, str]:
    """The window the log covers, as dates: its first event's day to the day after its last."""
    first, last = log.window()
    return first.date().isoformat(), (last.date() + timedelta(days=1)).isoformat()


def build(cfg: Config, log: Log) -> Plan:
    st = stats(log, cfg)
    plan = Plan(EXTRACTOR, EXTRACTOR_VERSION)
    first, last = log.window()
    vf, vt = period(log)
    median = round(statistics.median(st.cycle_days), 1) if st.cycle_days else 0.0
    case_word = cfg.case_object.lower() if cfg.case_object else "case"

    lines: list[str] = []
    at: dict[str, int] = {}

    def line(text: str, key: str | None = None) -> None:
        if key:
            at[key] = sum(len(x) + 1 for x in lines)
        lines.append(text)

    line(f"Event log digest: {cfg.process}")
    line(f"Log: {log.name} ({log.format}, sha256 {log.sha256})")
    line(
        f"Cases: {len(log.cases)} ({case_word}s), {log.events} events, from {first.date().isoformat()} "
        f"to {last.date().isoformat()}",
        "process",
    )
    if cfg.system:
        line(f"System: {cfg.system}")
    line(f"Median cycle time: {median} days (first to last event of a case)", "cycle")
    line("")
    line("Steps (executions, cases, roles):")
    steps = sorted(st.executions, key=lambda s: (-st.executions[s], s))
    for step in steps:
        labels = sorted(st.labels[step] - {step})
        shown = f" (label {', '.join(labels)})" if labels else ""
        roles = ", ".join(f"{r} {n}" for r, n in ordered(st.roles[step]))
        line(
            f"- {step}{shown}: {st.executions[step]} executions in {len(st.cases[step])} cases"
            + (f"; roles: {roles}" if roles else ""),
            f"step:{step}",
        )
    line("")
    line("Directly follows (times, cases, example cases):")
    pairs = sorted(st.follows, key=lambda p: (-st.follows[p], p))
    kept = [p for p in pairs if st.follows[p] >= cfg.min_count]
    for a, b in kept:
        line(
            f"- {a} -> {b}: {st.follows[(a, b)]} times in {len(st.follow_cases[(a, b)])} cases "
            f"({examples(st.follow_cases[(a, b)])})",
            f"flow:{a}->{b}",
        )
    dropped = [p for p in pairs if st.follows[p] < cfg.min_count]
    if dropped:
        line(f"Seen fewer than {cfg.min_count} times, not mapped:")
        for a, b in dropped:
            line(f"- {a} -> {b}: {st.follows[(a, b)]} times")
    line("")
    line("Starts: " + ", ".join(f"{s} {n}" for s, n in ordered(st.starts)))
    line("Ends: " + ", ".join(f"{s} {n}" for s, n in ordered(st.ends)))
    content = "\n".join(lines) + "\n"

    alias = f"{log.name}.digest"
    plan.source(
        Source(
            alias=alias,
            content=content,
            title=f"Event log digest: {cfg.process}",
            uri=f"event-log:{log.name}",
            collection=cfg.collection,
            origins=cfg.origins,
            metadata={
                "log": log.name,
                "format": log.format,
                "sha256": log.sha256,
                "cases": len(log.cases),
                "events": log.events,
                "from": first.isoformat(),
                "to": last.isoformat(),
            },
        )
    )

    def fact(text: str, key: str) -> Fact:
        return plan.fact(text, source=alias, at=at[key])

    process = plan.node("process", type="Entity", kind="process", namespace=NS, name=cfg.process)
    f = fact(
        f"{cfg.process} had {len(log.cases)} cases between {first.date().isoformat()} and "
        f"{last.date().isoformat()} in the event log {log.name}.",
        "process",
    )
    plan.mention(f, process)
    if cfg.case_object:
        data = plan.node("data", type="Entity", kind="data", name=cfg.case_object)
        plan.edge(f, "handles", process, data, {"qualifier": "case"})
    kpi(plan, f, f"Cases of {cfg.process}", process, len(log.cases), "cases", vf, vt)
    f = fact(
        f"The median cycle time of {cfg.process}, from a case's first event to its last, was {median} days "
        f"between {first.date().isoformat()} and {last.date().isoformat()}.",
        "cycle",
    )
    kpi(plan, f, f"Median cycle time of {cfg.process}", process, median, "days", vf, vt)

    system = plan.node("system", type="Entity", kind="component", name=cfg.system) if cfg.system else None
    for step in steps:
        node = plan.node(f"step:{step}", type="Entity", kind="activity", namespace=NS, name=step)
        roles = [r for r, _ in ordered(st.roles[step])]
        by = f" by the role{'s' if len(roles) > 1 else ''} {', '.join(roles)}" if roles else ""
        where = f", in {cfg.system}" if cfg.system else ""
        f = fact(
            f"{step} is a step of {cfg.process}, run {st.executions[step]} times in {len(st.cases[step])} "
            f"cases{by}{where}.",
            f"step:{step}",
        )
        plan.edge(f, "part_of", node, process)
        for role in roles:
            r = plan.node(f"role:{role}", type="Entity", kind="role", namespace=NS, name=role)
            plan.edge(f, "responsible_for", r, node)
        if system:
            plan.edge(f, "performed_in", node, system)
        kpi(plan, f, f"Executions of {step}", node, st.executions[step], "executions", vf, vt)
    for a, b in kept:
        f = fact(
            f"In {cfg.process}, {a} was directly followed by {b} {st.follows[(a, b)]} times in "
            f"{len(st.follow_cases[(a, b)])} cases.",
            f"flow:{a}->{b}",
        )
        plan.edge(f, "flows_to", f"step:{a}", f"step:{b}")
    return plan


def kpi(plan: Plan, fact: Fact, name: str, target: str, value: float, unit: str, vf: str, vt: str) -> None:
    node = plan.node(f"kpi:{name}", type="Entity", kind="kpi", namespace=NS, name=name)
    plan.edge(fact, "measures", node, target, {"value": value, "unit": unit}, valid_from=vf, valid_to=vt)

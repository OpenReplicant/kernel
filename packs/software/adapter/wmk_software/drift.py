"""Drift (ADR 0028): what a repository declares about a Compose stack, checked against a
capture of what runs.

`read_declared` reads the stack as the kernel holds it, through the gateway's
`query_graph`. `build` writes a drift digest and one observed verdict per declared edge it
checks, by `edge_id`, as conformance does (ADR 0027). Each verdict is an assertion or a
denial, so a later check can overturn an earlier denial.
- A declared `runs`: asserted when a container of the service runs that image with that
  tag or digest; denied when it runs another, or when no container runs. A job (a service
  others wait for to complete successfully) whose container finished cleanly counts as
  running.
- A declared published port (`exposed_by`): asserted when a container of the service
  publishes the port there; denied otherwise.
- A service that starts only with a Compose profile and has no running container is not
  checked.

Incidents are history, in an append-only source of their own: an `incident` opens for each
container that is unhealthy, restarting or exited with an error, keyed by `runtime`, and an
ongoing incident of a service whose containers all run cleanly again moves to `completed`.
Opening and closing cite the same source, so the newer status supersedes the older.

Where the repository and the observation disagree, the edge is contested: that is drift.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from wmk_software.docker import Capture, Container
from wmk_software.mapping import image_purl
from wmk_software.observe import collection, image_props
from wmk_software.plan import Plan, Source


class DeclaredError(Exception):
    """The gateway refused a read."""


@dataclass
class Edge:
    id: str
    service: str
    props: dict[str, Any]
    target: str  # the image or endpoint name


@dataclass
class Declared:
    stack: str
    offset: int
    profiles: dict[str, list[str]] = field(default_factory=dict)  # service -> profiles
    jobs: list[str] = field(default_factory=list)  # services others wait for to complete
    runs: list[Edge] = field(default_factory=list)
    ports: list[Edge] = field(default_factory=list)
    incidents: list[Edge] = field(default_factory=list)  # ongoing; target is the incident's name

    def fingerprint(self) -> str:
        """The declared structure, not its belief: an unchanged stack checked against an
        unchanged capture gives the same digest, which is skipped."""
        parts = [f"{s} {' '.join(ps)}" for s, ps in self.profiles.items()] + self.jobs
        parts += [
            f"{e.id} {e.service} {e.target} {json.dumps(e.props, sort_keys=True)}"
            for e in self.runs + self.ports
        ]
        parts += [f"{e.id} {e.service}" for e in self.incidents]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


SERVICES = """
MATCH (svc:Entity {kind: 'service'})-[p:part_of]->(st:Entity {kind: 'stack', name: $stack})
RETURN svc.name AS service, p.props AS props
"""
RUNS = """
MATCH (svc:Entity {kind: 'service'})-[:part_of]->(st:Entity {kind: 'stack', name: $stack}),
      (svc)-[r:depends_on]->(img:Entity {kind: 'image'})
WHERE r.kind = 'runs'
RETURN svc.name AS service, r.id AS id, r.props AS props, img.name AS target
"""
PORTS = """
MATCH (svc:Entity {kind: 'service'})-[:part_of]->(st:Entity {kind: 'stack', name: $stack}),
      (ep:Entity {kind: 'endpoint'})-[e:part_of]->(svc)
WHERE e.kind = 'exposed_by'
RETURN svc.name AS service, e.id AS id, e.props AS props, ep.name AS target
"""
JOBS = """
MATCH (a:Entity {kind: 'service'})-[n:depends_on]->(job:Entity {kind: 'service'}),
      (job)-[:part_of]->(st:Entity {kind: 'stack', name: $stack})
WHERE n.kind = 'needs'
RETURN job.name AS service, n.id AS id, n.props AS props, a.name AS target
"""
INCIDENTS = """
MATCH (svc:Entity {kind: 'service'})-[:part_of]->(st:Entity {kind: 'stack', name: $stack}),
      (svc)-[:participates_in]->(i:Event {kind: 'incident'})
WHERE i.status = 'ongoing'
RETURN svc.name AS service, i.id AS id, i.name AS target
"""


async def _query(client: Any, cypher: str, stack: str) -> dict[str, Any]:
    result = await client.call_tool(
        "query_graph", {"cypher": cypher, "params": {"stack": stack}, "limit": 1000}
    )
    body = json.loads(result.content[0].text)
    if result.is_error:
        raise DeclaredError(f"query_graph: {body.get('detail')}")
    if body["truncated"]:
        raise DeclaredError(f"the stack {stack} has more than 1000 parts; not checked")
    return body


def _edges(rows: list[dict[str, Any]]) -> list[Edge]:
    seen: dict[str, Edge] = {}
    for row in rows:
        seen.setdefault(row["id"], Edge(row["id"], row["service"], row.get("props") or {}, row["target"]))
    return sorted(
        seen.values(), key=lambda e: (e.service, e.target, json.dumps(e.props, sort_keys=True), e.id)
    )


async def read_declared(client: Any, stack: str) -> Declared:
    """The stack as the kernel holds it now: its services' profiles, images and ports."""
    services = await _query(client, SERVICES, stack)
    declared = Declared(stack, int(services["head_offset"]))
    profiles: dict[str, set[str]] = defaultdict(set)
    for row in services["rows"]:
        profiles[row["service"]].update((row.get("props") or {}).get("profiles") or [])
    declared.profiles = {s: sorted(p) for s, p in sorted(profiles.items())}
    declared.jobs = sorted(
        {
            row["service"]
            for row in (await _query(client, JOBS, stack))["rows"]
            if (row.get("props") or {}).get("condition") == "service_completed_successfully"
        }
    )
    declared.runs = _edges((await _query(client, RUNS, stack))["rows"])
    declared.ports = _edges((await _query(client, PORTS, stack))["rows"])
    declared.incidents = _edges((await _query(client, INCIDENTS, stack))["rows"])
    return declared


def same_image(declared: Edge, c: Container) -> bool:
    name, props = image_props(c.image)
    if image_purl(name) != image_purl(declared.target):
        return False
    want_digest, got_digest = declared.props.get("digest"), props.get("digest")
    if want_digest and want_digest != got_digest:
        return False
    if want_digest and "tag" not in declared.props:
        return True
    return declared.props.get("tag", "latest") == props.get("tag", "latest")


def same_port(declared: Edge, c: Container) -> bool:
    target = declared.props.get("target")
    protocol = declared.props.get("protocol", "tcp")
    want = declared.props.get("published")
    if want is not None:
        want = str(want).removeprefix("0.0.0.0:")
    return any(
        p.target == target and p.protocol == protocol and (want is None or p.published == want)
        for p in c.ports
    )


def build(cap: Capture, declared: Declared) -> Plan:
    plan = Plan()
    p = cap.project
    # The containers that count as the service running: running ones, and for a job (a
    # service others wait for to complete) those that finished cleanly.
    running: dict[str, list[Container]] = defaultdict(list)
    for c in cap.containers:
        name = f"{p}/{c.service}"
        if c.state == "running" or (c.finished and name in declared.jobs):
            running[name].append(c)
    seen = f"on {cap.host} at {cap.observed_at}"

    verdicts: list[tuple[str, str, bool, str]] = []  # digest line, edge id, holds, claim text
    skipped: list[str] = []

    def absent(e: Edge, what: str) -> bool:
        """True when no container runs the service; records a verdict or a skip for it."""
        if running[e.service]:
            return False
        profiles = declared.profiles.get(e.service) or []
        if profiles:
            skipped.append(f"- {e.service} {what}: it starts only with the {' and '.join(profiles)} profile.")
        else:
            verdicts.append(
                (
                    f"- {e.service} {what}: no container runs.",
                    e.id,
                    False,
                    f"No container of {e.service} was running {seen}, so it did not {what}.",
                )
            )
        return True

    for e in declared.runs:
        ref = (
            e.target
            + (f":{e.props['tag']}" if "tag" in e.props else "")
            + (f"@{e.props['digest']}" if "digest" in e.props else "")
        )
        if absent(e, f"run {ref}"):
            continue
        containers = running[e.service]
        names = ", ".join(sorted(c.name for c in containers))
        if any(same_image(e, c) for c in containers):
            verdicts.append(
                (
                    f"- {e.service} runs {ref}: yes ({names}).",
                    e.id,
                    True,
                    f"{e.service} was running {ref} in {names} {seen}.",
                )
            )
        else:
            actual = ", ".join(sorted({c.image for c in containers}))
            verdicts.append(
                (
                    f"- {e.service} runs {ref}: no, it runs {actual} ({names}).",
                    e.id,
                    False,
                    f"{e.service} was running {actual}, not {ref}, {seen}.",
                )
            )

    for e in declared.ports:
        where = e.props.get("published")
        what = f"publish port {e.props.get('target')}" + (f" on {where}" if where else "")
        if e.props.get("variables"):
            what += f" (set by {', '.join(e.props['variables'])})"
        if absent(e, what):
            continue
        containers = running[e.service]
        if any(same_port(e, c) for c in containers):
            verdicts.append((f"- {e.service} {what}: yes.", e.id, True, f"{e.service} did {what} {seen}."))
        else:
            actual = (
                ", ".join(sorted({f"{x.target} on {x.published}" for c in containers for x in c.ports}))
                or "nothing"
            )
            verdicts.append(
                (
                    f"- {e.service} {what}: no, it publishes {actual}.",
                    e.id,
                    False,
                    f"{e.service} did not {what} {seen}: it published {actual}.",
                )
            )

    lines = [
        f"Drift of the {p} stack on {cap.host}, observed {cap.observed_at}",
        f"Declared view: the stack {declared.stack}, {len(declared.runs)} images and "
        f"{len(declared.ports)} ports (fingerprint {declared.fingerprint()})",
        "",
        "Declared and observed:",
    ]
    at: list[int] = []

    def line(text: str) -> int:
        offset = sum(len(x) + 1 for x in lines)
        lines.append(text)
        return offset

    if not verdicts:
        line("- nothing checked")
    for text, *_ in verdicts:
        at.append(line(text))
    line("")
    line("Not checked:")
    for text in skipped or ["- nothing"]:
        line(text)

    alias = f"{p}@{cap.host}.drift"
    plan.source(
        Source(
            alias=alias,
            content="\n".join(lines) + "\n",
            title=f"Drift of {p} on {cap.host}, {cap.observed_at}",
            uri=f"docker://{cap.host}/{p}/drift",
            collection=collection(cap, "drift"),
            origins=(f"host:{cap.host}",),
            metadata={
                "host": cap.host,
                "project": p,
                "observed_at": cap.observed_at,
                "read_at_offset": declared.offset,
            },
        )
    )
    for offset, (_, edge_id, holds, claim) in zip(at, verdicts, strict=True):
        fact = plan.fact(claim, source=alias, at=offset)
        plan.on_edge(fact, edge_id, deny=not holds)
    incidents(plan, cap, declared, running)
    return plan


def since(c: Container, cap: Capture) -> str:
    """When the trouble began, as far as the capture shows."""
    if c.trouble == "unhealthy" and c.failing_since:
        return c.failing_since
    if c.finished_at and c.state != "restarting":
        return c.finished_at
    return c.started_at or cap.observed_at


def incidents(plan: Plan, cap: Capture, declared: Declared, running: dict[str, list[Container]]) -> None:
    """Open an incident per container in trouble; close the ongoing ones whose service runs
    cleanly again. Both in one append-only source, so the newer status supersedes."""
    p = cap.project
    lines = [f"Incidents of the {p} stack on {cap.host}, checked at {cap.observed_at}", ""]
    opened: list[tuple[int, Container]] = []
    closed: list[tuple[int, Edge, str]] = []
    for c in sorted(cap.containers, key=lambda c: (c.service, c.name)):
        if c.trouble:
            opened.append((sum(len(x) + 1 for x in lines), c))
            lines.append(f"- {p}/{c.service}: {c.name} ({c.short_id}) {c.trouble} since {since(c, cap)}")
    for e in declared.incidents:
        containers = running[e.service]
        if containers and all(c.trouble is None for c in containers):
            how = " and healthy" if any(c.shown_health for c in containers) else ""
            closed.append((sum(len(x) + 1 for x in lines), e, how))
            lines.append(f"- {e.target}: over; {e.service} runs{how}")
    if not opened and not closed:
        lines.append("- nothing opened or over")
    source = plan.source(
        Source(
            alias=f"{p}@{cap.host}.incidents",
            content="\n".join(lines) + "\n",
            title=f"Incidents of {p} on {cap.host}, {cap.observed_at}",
            uri=f"docker://{cap.host}/{p}/incidents",
            collection=collection(cap, "incidents"),
            origins=(f"host:{cap.host}",),
            metadata={"host": cap.host, "project": p, "observed_at": cap.observed_at},
            append_only=True,
        )
    )
    for at, c in opened:
        when = since(c, cap)
        svc = plan.node(
            f"svc:{p}/{c.service}",
            type="Entity",
            kind="service",
            namespace="software",
            name=f"{p}/{c.service}",
        )
        key = plan.node(
            f"incident:{c.short_id}@{when}",
            type="Event",
            kind="incident",
            namespace="software",
            name=f"{p}/{c.service} {c.trouble} since {when}",
            identity={"runtime": f"{cap.host}/{c.short_id}@{when}"},
            start=when,
            status="ongoing",
            props={"container": c.name, "host": cap.host, "state": c.state},
        )
        fact = plan.fact(
            f"On {cap.host}, the container {c.name} of {p}/{c.service} was {c.trouble} from {when} "
            f"(seen at {cap.observed_at}).",
            source=source.alias,
            at=at,
            once=True,
        )
        plan.edge(fact, "participates_in", svc, key, {"role": "affected"})
    for at, e, how in closed:
        fact = plan.fact(
            f"{e.service} was running again{how} on {cap.host} at {cap.observed_at}, so the incident "
            f"'{e.target}' is over.",
            source=source.alias,
            at=at,
        )
        plan.transition(fact, e.id, "completed")

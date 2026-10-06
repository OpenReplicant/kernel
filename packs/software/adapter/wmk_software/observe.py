"""What runs, as observed (ADR 0028): a Docker capture mapped without reading the model.

Two sources, both from the host as origin:
- **The state now**, mapped in an extraction run in a stable collection, so the next
  capture retracts what no longer runs. The service of each running container, or of a
  container that finished with code 0 (a job), is `part_of` its stack, `runs` its image
  (the same props the declared view uses: tag or digest), has its published ports as
  endpoints `exposed_by` it, and `runs_on` the host.
- **The history**, append-only like git history: a `deployment` per container at its
  creation time, written once, keyed by `runtime`. When the image carries a revision label,
  the change at that commit is `part_of` the deployment (ADR 0031). Incidents need what the
  kernel already holds (to close them), so `drift` writes them.

Observed nodes reuse the declared ones by name. With `repo`, stacks, services and
endpoints also carry the declared identity keys (`<repository URL>#<project>/<service>`).
"""

from __future__ import annotations

from typing import Any

from wmk_software.docker import Capture, Container
from wmk_software.mapping import split_ref
from wmk_software.plan import Plan, Source

NS = "software"


def collection(cap: Capture, part: str) -> str:
    return f"docker://{cap.host}/{cap.project}#{part}"


def image_props(ref: str) -> tuple[str, dict[str, Any]]:
    """The image's name and the props a `runs` edge carries for this reference."""
    name, tag, digest = split_ref(ref)
    return name, {k: v for k, v in (("tag", tag), ("digest", digest)) if v}


def published(c: Container) -> str:
    if not c.ports:
        return ""
    return "; publishes " + ", ".join(f"{p.target}/{p.protocol} on {p.published}" for p in c.ports)


def describe(c: Container) -> str:
    health = f", {c.shown_health}" if c.shown_health else ""
    code = f" with code {c.exit_code}" if c.state == "exited" else ""
    restarts = f", restarted {c.restarts} times" if c.restarts else ""
    image_id = f" ({c.image_id.removeprefix('sha256:')[:12]})" if c.image_id else ""
    return (
        f"container {c.name} ({c.short_id}), {c.state}{code}{health}{restarts}, image {c.image}{image_id}, "
        f"created {c.created}"
    )


class Observer:
    def __init__(self, cap: Capture, repo: str | None) -> None:
        self.cap = cap
        self.repo = repo
        self.plan = Plan()

    def node(self, key: str, kind: str, name: str, *, defined_at: str | None = None, **extra: Any) -> str:
        spec: dict[str, Any] = {"type": "Entity", "kind": kind, "namespace": NS, "name": name, **extra}
        if defined_at and self.repo:
            spec["identity"] = {"defined_at": f"{self.repo}#{defined_at}"}
        return self.plan.node(key, **spec)

    def image(self, name: str) -> str:
        # Built images are declared by name alone, registry images with a package URL: the
        # observation cannot tell them apart, so it names the image and lets the kernel's
        # resolution find the declared node.
        return self.plan.node(f"img:{name}", type="Entity", kind="image", namespace=NS, name=name)

    def build(self) -> Plan:
        cap = self.cap
        p = cap.project
        state_lines = [
            f"Docker on {cap.host}, Compose project {p}, observed {cap.observed_at}",
            f"Containers: {len(cap.containers)} ({sum(c.state == 'running' for c in cap.containers)} "
            "running)",
            "",
        ]
        history_lines = [
            f"Docker on {cap.host}, Compose project {p}: what happened, as seen at {cap.observed_at}",
            "",
        ]
        state_at: dict[str, int] = {}
        history_at: dict[str, int] = {}
        for c in cap.containers:
            state_at[c.id] = sum(len(x) + 1 for x in state_lines)
            state_lines.append(f"- {p}/{c.service}: {describe(c)}{published(c)}")
        for c in sorted(cap.containers, key=lambda c: (c.created, c.name)):
            history_at[c.id] = sum(len(x) + 1 for x in history_lines)
            revision = f", revision {c.revision}" if c.revision else ""
            history_lines.append(
                f"- {c.created}: {c.name} ({c.short_id}) created for {p}/{c.service} from {c.image}{revision}"
            )

        origins = (f"host:{cap.host}",)
        state = self.plan.source(
            Source(
                alias=f"{p}@{cap.host}.state",
                content="\n".join(state_lines) + "\n",
                title=f"Docker on {cap.host}: {p}, observed {cap.observed_at}",
                uri=f"docker://{cap.host}/{p}",
                collection=collection(cap, "state"),
                origins=origins,
                metadata={"host": cap.host, "project": p, "observed_at": cap.observed_at},
            )
        )
        history = self.plan.source(
            Source(
                alias=f"{p}@{cap.host}.history",
                content="\n".join(history_lines) + "\n",
                title=f"Docker on {cap.host}: what happened to {p}",
                uri=f"docker://{cap.host}/{p}/history",
                collection=collection(cap, "history"),
                origins=origins,
                metadata={"host": cap.host, "project": p, "observed_at": cap.observed_at},
                append_only=True,
            )
        )

        stack = self.node(f"stack:{p}", "stack", p, defined_at=p)
        host = self.plan.node(f"host:{cap.host}", type="Entity", kind="host", namespace=NS, name=cap.host)
        for c in cap.containers:
            if c.state != "running" and not c.finished:
                continue
            svc = self.service(c.service)
            name, props = image_props(c.image)
            how = f" ({c.shown_health})" if c.shown_health else ""
            did = (
                f"was running {c.image} in the container {c.name}{how}"
                if c.state == "running"
                else f"had run {c.image} in the container {c.name}, which finished with code 0"
            )
            fact = self.plan.fact(
                f"On {cap.host} at {cap.observed_at}, {p}/{c.service} {did}.",
                source=state.alias,
                at=state_at[c.id],
            )
            self.plan.edge(fact, "part_of", svc, stack)
            self.plan.edge(fact, "runs", svc, self.image(name), props or None)
            self.plan.edge(fact, "runs_on", svc, host)
            for port in c.ports:
                full = f"{p}/{c.service}:{port.target}"
                ep = self.node(f"ep:{full}", "endpoint", full, defined_at=full)
                eprops: dict[str, Any] = {"target": port.target, "published": port.published}
                if port.protocol != "tcp":
                    eprops["protocol"] = port.protocol
                self.plan.edge(fact, "exposed_by", ep, svc, eprops)

        for c in cap.containers:
            svc = self.service(c.service)
            name, _ = image_props(c.image)
            key = f"deployment:{c.short_id}"
            self.plan.node(
                key,
                type="Event",
                kind="deployment",
                namespace=NS,
                name=f"{p}/{c.service} from {c.image} at {c.created}",
                identity={"runtime": f"{cap.host}/{c.short_id}"},
                start=c.created,
                props={"container": c.name, "image_id": c.image_id, "host": cap.host},
            )
            fact = self.plan.fact(
                f"On {cap.host}, the container {c.name} was created for {p}/{c.service} from {c.image} at "
                f"{c.created}.",
                source=history.alias,
                at=history_at[c.id],
                once=True,
            )
            self.plan.edge(fact, "participates_in", svc, key, {"role": "deployed"})
            self.plan.edge(fact, "participates_in", self.image(name), key, {"role": "image"})
            if c.revision:
                change = self.plan.node(
                    f"commit:{c.revision[:12]}",
                    type="Event",
                    kind="change",
                    namespace=NS,
                    name=f"Commit {c.revision[:7]}",
                    identity={"commit": c.revision},
                    props={"commit": c.revision},
                )
                rev = self.plan.fact(
                    f"The container {c.name} on {cap.host} runs code at commit {c.revision[:7]}, its image's "
                    "revision.",
                    source=history.alias,
                    at=history_at[c.id],
                    once=True,
                )
                self.plan.edge(rev, "part_of", change, key)
        self.plan.drop_empty()
        return self.plan

    def service(self, name: str) -> str:
        full = f"{self.cap.project}/{name}"
        return self.node(f"svc:{full}", "service", full, defined_at=full)


def build(cap: Capture, *, repo: str | None = None) -> Plan:
    return Observer(cap, repo).build()

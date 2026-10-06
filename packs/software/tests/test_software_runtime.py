"""What runs against what is declared, through the gateway (ADR 0028).
- A stack declared by its repository and observed running another image tag, on another
  port, is contested: drift.
- A finished job counts as running; a profile-only service is not checked.
- An unhealthy container opens an incident that the next healthy check closes.
- Observing and checking an unchanged stack again writes nothing."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from kernel.testing import KernelDB
from wmk_software import drift, observe
from wmk_software.apply import apply
from wmk_software.docker import Capture, Container, Port
from wmk_software.mapping import build
from wmk_software.repo import Repository

pytestmark = pytest.mark.anyio

URL = "https://github.com/example/toy"
COMPOSE = """\
name: toy
services:
  web:
    image: nginx:1.27
    ports: ["127.0.0.1:8080:80"]
    depends_on:
      cache: {condition: service_healthy}
      migrate: {condition: service_completed_successfully}
  cache:
    image: redis:7
  migrate:
    image: example/app:1
  docs:
    image: example/docs:1
    profiles: [docs]
"""


def repository() -> Repository:
    history = f"Repository: {URL}\nBranch: main\n\nFirst-parent commits, newest first:\n\n"
    history += f"{'a' * 40} 2026-10-01T12:00:00Z Add the toy stack (#1)\n"
    return Repository(
        url=URL,
        name="example/toy",
        commit="a" * 40,
        branch="main",
        files={"compose.yaml": COMPOSE},
        history=history,
    )


def box(service: str, image: str, n: int, **kw: Any) -> Container:
    return Container(
        id=f"{n:x}" * 64,
        name=f"toy-{service}-1",
        service=service,
        image=image,
        image_id=f"sha256:{n:x}" * 8,
        created="2026-10-06T09:00:00Z",
        **{"state": "running", **kw},
    )


WEB = box("web", "nginx:1.26", 1, ports=(Port(80, "tcp", "127.0.0.1:8081"),))
CACHE = box("cache", "redis:7", 2, health="healthy")
MIGRATE = box("migrate", "example/app:1", 3, state="exited", finished_at="2026-10-06T09:00:30Z")
DEBUG = box("debug", "busybox:1", 4)


def capture(at: str, *containers: Container) -> Capture:
    return Capture("ci-host", "toy", at, list(containers))


async def run(gateway: Any, cap: Capture) -> tuple[Any, Any]:
    observed = await apply(gateway, observe.build(cap, repo=URL))
    checked = await apply(gateway, drift.build(cap, await drift.read_declared(gateway, "toy")))
    assert observed.failures == [] and checked.failures == [], (observed.failures, checked.failures)
    return observed, checked


def runs(kdb: KernelDB, service: str) -> dict[str, tuple[Any, ...]]:
    rows = kdb.q(
        "SELECT e.props ->> 'tag', e.belief_status, e.origins_for, e.origins_against FROM kernel.edges e "
        "JOIN kernel.nodes f ON f.id = e.from_id WHERE e.kind = 'runs' AND f.name = %s",
        [service],
    )
    return {tag: tuple(rest) for tag, *rest in rows}


async def test_drift_is_contested_and_checking_again_writes_nothing(gateway: Any, kdb: KernelDB) -> None:
    assert (await apply(gateway, build(repository()))).failures == []
    cap = capture("2026-10-06T10:00:00Z", WEB, CACHE, MIGRATE, DEBUG)
    _, checked = await run(gateway, cap)
    digest = drift.build(cap, await drift.read_declared(gateway, "toy")).sources[0].content
    assert "- toy/web runs nginx:1.27: no, it runs nginx:1.26 (toy-web-1)." in digest
    assert "- toy/web publish port 80 on 127.0.0.1:8080: no, it publishes 80 on 127.0.0.1:8081." in digest
    assert "- toy/docs run example/docs:1: it starts only with the docs profile." in digest
    assert checked.denied == 2

    # The repository says nginx 1.27; the host runs 1.26. One origin on each side.
    assert runs(kdb, "toy/web") == {"1.27": ("contested", 1, 1), "1.26": ("accepted", 1, 0)}
    # Where they agree, two origins: the repository and the host. A finished job counts.
    assert runs(kdb, "toy/cache") == {"7": ("accepted", 2, 0)}
    assert runs(kdb, "toy/migrate") == {"1": ("accepted", 2, 0)}
    # Not running and not expected to: not checked, so only the repository's word.
    assert runs(kdb, "toy/docs") == {"1": ("accepted", 1, 0)}
    ports = kdb.q(
        "SELECT e.props ->> 'published', e.belief_status FROM kernel.edges e "
        "WHERE e.kind = 'exposed_by' ORDER BY 1"
    )
    assert ports == [("127.0.0.1:8080", "contested"), ("127.0.0.1:8081", "accepted")]
    # Running but declared nowhere: the host's word alone, with the declared identity key.
    debug = kdb.q("SELECT identity ->> 'defined_at' FROM kernel.nodes WHERE name = 'toy/debug'")
    assert debug == [(f"{URL}#toy/debug",)]
    deployments = kdb.q(
        "SELECT count(*) FROM kernel.nodes WHERE kind = 'deployment' AND identity ? 'runtime'"
    )
    assert deployments == [(4,)]

    again_observed, again_checked = await run(gateway, cap)
    assert (again_observed.claims, again_checked.claims) == (0, 0)


async def test_an_incident_opens_and_the_next_healthy_check_closes_it(gateway: Any, kdb: KernelDB) -> None:
    assert (await apply(gateway, build(repository()))).failures == []
    sick = replace(CACHE, health="unhealthy", failing_since="2026-10-06T10:55:00Z")
    await run(gateway, capture("2026-10-06T11:00:00Z", WEB, sick, MIGRATE))
    incident = kdb.q("SELECT name, status FROM kernel.nodes WHERE kind = 'incident'")
    assert incident == [("toy/cache unhealthy since 2026-10-06T10:55:00Z", "ongoing")]
    # Still sick at the next check: the same incident, still open.
    await run(gateway, capture("2026-10-06T11:05:00Z", WEB, sick, MIGRATE))
    assert kdb.q("SELECT status FROM kernel.nodes WHERE kind = 'incident'") == [("ongoing",)]

    await run(gateway, capture("2026-10-06T11:10:00Z", WEB, CACHE, MIGRATE))
    assert kdb.q("SELECT status FROM kernel.nodes WHERE kind = 'incident'") == [("completed",)]
    assert any("is over" in text for (text,) in kdb.q("SELECT text FROM kernel.claims_view"))
    # The affected service stays on record: history is never retracted.
    affected = kdb.q(
        "SELECT e.belief_status FROM kernel.edges e JOIN kernel.nodes i ON i.id = e.to_id "
        "WHERE i.kind = 'incident'"
    )
    assert affected == [("accepted",)]


async def test_drift_needs_nothing_declared(gateway: Any) -> None:
    # Observed alone, nothing is declared: no verdicts, and the digest says so.
    cap = capture("2026-10-06T12:00:00Z", WEB)
    await apply(gateway, observe.build(cap))
    declared = await drift.read_declared(gateway, "toy")
    digest = drift.build(cap, declared).sources[0].content
    assert "Declared and observed:\n- toy/web runs nginx:1.26: yes (toy-web-1)." in digest

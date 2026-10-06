"""Docker captures without a daemon or a database: only allowlisted fields are kept (never
the environment), times and ports are normalised, health and exits read correctly, and the
recorded fixture regenerates from its capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wmk_software import docker, drift, fixture, observe
from wmk_software.docker import Capture, Container, Port, container, ports, stamp

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "kernel-runtime"


def inspect(**over: Any) -> dict[str, Any]:
    """A `docker inspect` result as the Engine API returns it, with things that must never be kept."""
    doc: dict[str, Any] = {
        "Id": "48fd0df61f792328b7e8f0951f89a2f8a3238aea71132731c7f4e34b6d3fa728",
        "Created": "2026-10-06T04:27:45.123456789Z",
        "Name": "/wmk-gateway-1",
        "Image": "sha256:fcf410e98b56aa",
        "RestartCount": 0,
        "Path": "wmk-gateway",
        "Args": ["--token", "s3cret"],
        "Config": {
            "Image": "wmk-gateway:dev",
            "Env": ["WMK_DB_PASSWORD=hunter2", "PATH=/usr/bin"],
            "Cmd": ["wmk-gateway"],
            "Labels": {
                "com.docker.compose.project": "wmk",
                "com.docker.compose.service": "gateway",
                "com.docker.compose.project.working_dir": "/home/someone/kernel",
                "maintainer": "someone@example.com",
            },
        },
        "Mounts": [{"Source": "/home/someone/.ssh", "Destination": "/root/.ssh"}],
        "State": {
            "Status": "running",
            "ExitCode": 0,
            "StartedAt": "2026-10-06T04:27:46.5Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "Health": {
                "Status": "healthy",
                "FailingStreak": 0,
                "Log": [{"Start": "2026-10-06T04:28:00Z", "ExitCode": 0, "Output": "token=abc"}],
            },
        },
        "NetworkSettings": {
            "Ports": {
                "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
                "9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9000"}, {"HostIp": "::", "HostPort": "9000"}],
                "9100/udp": [{"HostIp": "::1", "HostPort": "9100"}],
                "5000/tcp": None,
            }
        },
    }
    for key, value in over.items():
        doc[key] = value
    return doc


def test_a_capture_keeps_only_the_allowlist() -> None:
    c = container(inspect())
    text = Capture("dev-host", "wmk", "2026-10-06T04:30:00Z", [c]).to_json()
    for secret in ("hunter2", "s3cret", "PASSWORD", "someone", ".ssh", "token=abc", "maintainer"):
        assert secret not in text, secret
    assert (c.name, c.service, c.image, c.created, c.started_at) == (
        "wmk-gateway-1",
        "gateway",
        "wmk-gateway:dev",
        "2026-10-06T04:27:45Z",
        "2026-10-06T04:27:46Z",
    )
    # Bound on every address, the IPv6 twin dropped; a single IPv6 address is not kept.
    assert c.ports == (Port(8000, "tcp", "127.0.0.1:8000"), Port(9000, "tcp", "9000"))
    assert c.finished_at is None and c.trouble is None and c.shown_health == "healthy"


def test_times_ports_health_and_exits_read_correctly() -> None:
    assert stamp("2026-10-06T06:27:45.5+02:00") == "2026-10-06T04:27:45Z"
    assert stamp("0001-01-01T00:00:00Z") is None
    assert ports({"80/tcp": [{"HostIp": "", "HostPort": "8080"}]}) == (Port(80, "tcp", "8080"),)
    unhealthy = container(
        inspect(
            State={
                "Status": "running",
                "ExitCode": 0,
                "StartedAt": "2026-10-06T04:00:00Z",
                "Health": {
                    "Status": "unhealthy",
                    "Log": [
                        {"Start": "2026-10-06T04:10:00Z", "ExitCode": 0},
                        {"Start": "2026-10-06T04:11:00Z", "ExitCode": 1},
                        {"Start": "2026-10-06T04:12:00Z", "ExitCode": 1},
                    ],
                },
            }
        )
    )
    assert (unhealthy.trouble, unhealthy.failing_since) == ("unhealthy", "2026-10-06T04:11:00Z")
    job = container(
        inspect(
            State={
                "Status": "exited",
                "ExitCode": 0,
                "FinishedAt": "2026-10-06T04:28:00Z",
                "Health": {"Status": "unhealthy"},
            }
        )
    )
    # A finished job's stale healthcheck says nothing.
    assert job.finished and job.trouble is None and job.shown_health is None
    failed = container(
        inspect(State={"Status": "exited", "ExitCode": 3, "FinishedAt": "2026-10-06T04:28:00Z"})
    )
    assert (failed.finished, failed.trouble, failed.finished_at) == (
        False,
        "exited with code 3",
        "2026-10-06T04:28:00Z",
    )


def test_captures_round_trip_and_refuse_other_documents(tmp_path: Path) -> None:
    cap = docker.load(FIXTURE / "capture.json")
    assert (cap.host, cap.project, len(cap.containers)) == ("dev-host", "wmk", 3)
    (tmp_path / "again.json").write_text(cap.to_json())
    assert docker.load(tmp_path / "again.json") == cap
    (tmp_path / "other.json").write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(docker.CaptureError, match="not a wmk-docker-capture"):
        docker.load(tmp_path / "other.json")


def test_the_runtime_fixture_regenerates_from_its_capture(tmp_path: Path) -> None:
    out = tmp_path / "fixture"
    out.mkdir()
    (out / "fixture.yaml").write_text((FIXTURE / "fixture.yaml").read_text())
    fixture.write_observed(
        FIXTURE / "capture.json", out, name="kernel-runtime", repo="https://github.com/OpenReplicant/kernel"
    )
    names = ["fixture.yaml", "script.yaml", "capture.json"] + [
        f"sources/{p.name}" for p in (FIXTURE / "sources").iterdir()
    ]
    for name in names:
        assert (out / name).read_text() == (FIXTURE / name).read_text(), name


def test_observe_writes_state_in_a_run_and_history_once() -> None:
    cap = docker.load(FIXTURE / "capture.json")
    plan = observe.build(cap)
    state, history = plan.sources
    assert (state.append_only, history.append_only) == (False, True)
    assert state.collection == "docker://dev-host/wmk#state" and state.origins == ("host:dev-host",)
    # Without --repo, observed nodes carry no identity and meet the declared ones by name.
    assert all(
        "identity" not in spec for key, spec in plan.nodes.items() if key.startswith(("svc:", "stack:"))
    )
    deployments = [f for f in plan.facts if f.source == history.alias]
    assert len(deployments) == 3 and all(f.once for f in deployments)
    # A container that failed is neither running nor done: no state, only its deployment.
    stopped = Container(
        "f" * 64,
        "wmk-papers-1",
        "papers",
        "wmk-papers:dev",
        "",
        "2026-10-06T04:00:00Z",
        "exited",
        finished_at="2026-10-06T04:30:00Z",
        exit_code=137,
    )
    failed = Capture("dev-host", "wmk", "2026-10-06T05:00:00Z", [*cap.containers, stopped])
    plan = observe.build(failed)
    assert [f.source for f in plan.facts if "wmk/papers" in f.text] == [history.alias]
    # The incident is drift's: it needs what the kernel holds to close it later.
    plan = drift.build(failed, drift.Declared("wmk", 0))
    incidents = plan.sources[-1]
    assert (incidents.collection, incidents.append_only) == ("docker://dev-host/wmk#incidents", True)
    opened = [f for f in plan.facts if f.source == incidents.alias]
    assert [f.text for f in opened] == [
        "On dev-host, the container wmk-papers-1 of wmk/papers was exited with code 137 from "
        "2026-10-06T04:30:00Z (seen at 2026-10-06T05:00:00Z)."
    ]
    assert opened[0].once
    incident = plan.nodes["incident:ffffffffffff@2026-10-06T04:30:00Z"]
    assert (incident["status"], incident["identity"]) == (
        "ongoing",
        {"runtime": "dev-host/ffffffffffff@2026-10-06T04:30:00Z"},
    )

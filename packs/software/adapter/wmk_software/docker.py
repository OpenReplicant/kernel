"""Captures of what runs on Docker (ADR 0028): the containers of one Compose project, read
from the Docker Engine API over its unix socket and reduced to an allowlist.

Only these fields are kept: id, name, creation time, the image reference and id, the
Compose labels, state, health, restart count and published ports. Environment variables,
commands, mounts and other labels are never read into a capture: they hold secrets. A
capture is a JSON document; tests and fixtures replay recorded ones, so nothing but
`capture` needs a Docker daemon.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "wmk-docker-capture"
VERSION = 1
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"


class CaptureError(Exception):
    """Docker could not be read, or a capture file is not one."""


@dataclass(frozen=True)
class Port:
    target: int
    protocol: str
    published: str  # "<host ip>:<port>", or "<port>" when bound on every address


@dataclass(frozen=True)
class Container:
    id: str
    name: str
    service: str
    image: str  # the reference the container was created from, such as wmk-gateway:dev
    image_id: str
    created: str
    state: str  # running, restarting, exited, paused, created, dead
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int = 0
    health: str | None = None  # healthy, unhealthy, starting; None without a healthcheck
    failing_since: str | None = None  # start of the current run of failed health checks
    restarts: int = 0
    ports: tuple[Port, ...] = ()

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def trouble(self) -> str | None:
        """Why this container needs attention, or None."""
        if self.state == "restarting":
            return "restarting"
        if self.state in ("exited", "dead") and self.exit_code != 0:
            return f"exited with code {self.exit_code}"
        if self.state == "running" and self.health == "unhealthy":
            return "unhealthy"
        return None

    @property
    def finished(self) -> bool:
        """Ran to completion: a job's container that exited cleanly."""
        return self.state == "exited" and self.exit_code == 0

    @property
    def shown_health(self) -> str | None:
        """Health as it stands: only a running container's healthcheck says anything."""
        return self.health if self.state == "running" else None


@dataclass
class Capture:
    host: str
    project: str
    observed_at: str
    containers: list[Container] = field(default_factory=list)

    def to_json(self) -> str:
        doc = {"format": FORMAT, "version": VERSION, **asdict(self)}
        return json.dumps(doc, indent=2, sort_keys=False) + "\n"


def stamp(value: str | None) -> str | None:
    """A Docker time (nanoseconds, any offset) as ISO 8601 UTC to the second; None for the
    zero time Docker uses for "never"."""
    if not value or value.startswith("0001-01-01"):
        return None
    text = re.sub(r"\.\d+", "", value, count=1).replace("Z", "+00:00")
    t = datetime.fromisoformat(text)
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ports(bindings: dict[str, Any] | None) -> tuple[Port, ...]:
    """Published ports from NetworkSettings.Ports; IPv6 duplicates of IPv4 bindings dropped."""
    out: set[Port] = set()
    for key, binds in (bindings or {}).items():
        if not binds:
            continue
        target, _, protocol = key.partition("/")
        for b in binds:
            ip, port = b.get("HostIp") or "", b.get("HostPort") or ""
            if not port or (":" in ip and ip != "::"):
                continue  # a specific IPv6 address
            if ip == "::" and any(x.get("HostIp") in ("", "0.0.0.0") for x in binds):
                continue
            published = port if ip in ("", "0.0.0.0", "::") else f"{ip}:{port}"
            out.add(Port(int(target), protocol or "tcp", published))
    return tuple(sorted(out, key=lambda p: (p.target, p.protocol, p.published)))


def failing_since(health: dict[str, Any] | None) -> str | None:
    """When the current run of failed health checks began (the log keeps the last few)."""
    if not health or health.get("Status") != "unhealthy":
        return None
    since = None
    for entry in reversed(health.get("Log") or []):
        if entry.get("ExitCode") == 0:
            break
        since = entry.get("Start")
    return stamp(since)


def container(inspect: dict[str, Any]) -> Container:
    """The allowlisted fields of one `docker inspect` (GET /containers/{id}/json) result."""
    config = inspect.get("Config") or {}
    labels = config.get("Labels") or {}
    state = inspect.get("State") or {}
    health = state.get("Health")
    return Container(
        id=str(inspect["Id"]),
        name=str(inspect.get("Name", "")).lstrip("/"),
        service=str(labels.get(SERVICE) or str(inspect.get("Name", "")).lstrip("/")),
        image=str(config.get("Image") or ""),
        image_id=str(inspect.get("Image") or ""),
        created=stamp(inspect.get("Created")) or "",
        state=str(state.get("Status") or "unknown"),
        started_at=stamp(state.get("StartedAt")),
        finished_at=stamp(state.get("FinishedAt")) if state.get("Status") != "running" else None,
        exit_code=int(state.get("ExitCode") or 0),
        health=(health or {}).get("Status"),
        failing_since=failing_since(health),
        restarts=int(inspect.get("RestartCount") or 0),
        ports=ports((inspect.get("NetworkSettings") or {}).get("Ports")),
    )


def load(path: Path | str) -> Capture:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"cannot read {path}: {exc}") from exc
    if doc.get("format") != FORMAT or doc.get("version") != VERSION:
        raise CaptureError(f"{path} is not a {FORMAT} version {VERSION} document")
    containers = [
        Container(**{**c, "ports": tuple(Port(**p) for p in c.get("ports", []))}) for c in doc["containers"]
    ]
    return Capture(doc["host"], doc["project"], doc["observed_at"], containers)


# Reading a live daemon --------------------------------------------------------------------


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("localhost", timeout=10)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(self._path)
        self.sock = sock


def _socket() -> str:
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not host.startswith("unix://"):
        raise CaptureError(f"DOCKER_HOST {host} is not a unix socket; only the local daemon is read")
    return host.removeprefix("unix://")


def _get(path: str) -> Any:
    conn = _UnixConnection(_socket())
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    except OSError as exc:
        raise CaptureError(f"cannot reach the Docker daemon: {exc}") from exc
    finally:
        conn.close()
    if response.status != 200:
        raise CaptureError(f"Docker answered {response.status} to GET {path}")
    return json.loads(body)


def capture(project: str, *, host: str | None = None, now: datetime | None = None) -> Capture:
    """The containers of a Compose project on the local daemon, now."""
    filters = urllib.parse.quote(json.dumps({"label": [f"{PROJECT}={project}"]}))
    listed = _get(f"/containers/json?all=1&filters={filters}")
    containers = [container(_get(f"/containers/{c['Id']}/json")) for c in listed]
    containers.sort(key=lambda c: (c.service, c.name))
    name = host or str(_get("/info").get("Name") or "docker")
    at = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Capture(name, project, at, containers)

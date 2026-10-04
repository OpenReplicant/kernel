"""ACP profiles: small config files describing one way to run a session.

The gateway reads the identity part. Each machine profile is also an Agent node, so
its writes are attributed to a known configuration; the interactive profile also
names the person, who becomes a human Agent and the author of transcript turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

TrustLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: Literal["human", "machine"]
    trust_level: TrustLevel = "medium"
    identity: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    name: str
    agent: AgentSpec
    person: AgentSpec | None = None
    transcript_capture: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def load(path: Path) -> Profile:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "name" not in data or "agent" not in data:
        raise ValueError(f"{path}: a profile needs name and agent")
    name = str(data["name"])
    agent = _agent(data["agent"], default_kind="machine")
    if agent.kind != "machine":
        raise ValueError(f"{path}: the profile agent must be a machine agent")
    # A machine profile is identified by its profile name.
    agent = AgentSpec(agent.name, agent.kind, agent.trust_level, {**agent.identity, "profile": name})
    identity = data.get("identity") or {}
    person = _agent(identity["person"], default_kind="human") if identity.get("person") else None
    capture = data.get("transcript_capture") or {}
    return Profile(
        name=name,
        agent=agent,
        person=person,
        transcript_capture=bool(capture.get("enabled", False))
        if isinstance(capture, dict)
        else bool(capture),
        raw=data,
    )


def _agent(data: Any, default_kind: str) -> AgentSpec:
    if not isinstance(data, dict) or not data.get("name"):
        raise ValueError("an agent needs a name")
    kind = data.get("kind", default_kind)
    if kind not in ("human", "machine"):
        raise ValueError("agent kind must be human or machine")
    trust = data.get("trust_level", "medium")
    if trust not in ("low", "medium", "high"):
        raise ValueError("trust_level must be low, medium or high")
    identity = {str(k): str(v) for k, v in (data.get("identity") or {}).items()}
    return AgentSpec(str(data["name"]), kind, trust, identity)

"""Agent registration for a profile: the machine agent (and, interactively, the person).

Registration goes through kernel.write like every other change. The machine agent
registers itself (a create with "self": true); the person is registered by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway.db import Kernel
from gateway.problems import KernelRejection
from gateway.profiles import AgentSpec, Profile


@dataclass(frozen=True)
class Agents:
    agent_id: str
    person_id: str | None = None


async def ensure_agents(kernel: Kernel, profile: Profile) -> Agents:
    agent_id = await _ensure(
        kernel,
        profile.agent,
        writer=None,
        claim_text=f"The {profile.name} profile runs as a machine agent of the kernel.",
    )
    person_id = None
    if profile.person is not None:
        person_id = await _ensure(
            kernel,
            profile.person,
            writer=agent_id,
            claim_text=f"A person works with the kernel through the {profile.name} profile.",
        )
    return Agents(agent_id, person_id)


async def _ensure(kernel: Kernel, spec: AgentSpec, writer: str | None, claim_text: str) -> str:
    found = await _find(kernel, spec)
    if found:
        return found
    op: dict[str, Any] = {
        "op": "create",
        "type": "Agent",
        "kind": spec.kind,
        "name": spec.name,
        "trust_level": spec.trust_level,
    }
    if spec.identity:
        op["identity"] = spec.identity
    if writer is None:
        op["self"] = True
    payload = {
        "claim": {"text": claim_text, "basis": "observed", "modality": "descriptive"},
        "read_at_offset": await kernel.head_offset(),
        "ops": [op],
    }
    try:
        result = await kernel.write(payload, writer)
    except KernelRejection as rejection:
        # Another gateway registered the same agent first.
        if rejection.detail.get("problem") in ("duplicate", "stale"):
            found = await _find(kernel, spec)
            if found:
                return found
        raise
    return str(result["refs"].get("$self") or result["ops"][0]["id"])


async def _find(kernel: Kernel, spec: AgentSpec) -> str | None:
    if spec.identity:
        return await kernel.find_agent(spec.identity)
    candidates = await kernel.resolve_candidates(spec.name, "Agent", spec.kind, {}, None, 1)
    if candidates and candidates[0]["stage"] == "normalized":
        return str(candidates[0]["node_id"])
    return None

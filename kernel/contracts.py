"""Slot contracts for modules written in Python.

The wire formats are the JSON Schemas in kernel/schemas/ (change sets, module manifests,
chains); they are the source of truth for every language. These Protocols are the Python
shape of the same contracts, so Python modules type-check against them. Modules written as
RuleGo chains follow the same contracts through HTTP.

Rules every implementation follows (docs/spec.md §3-§5):
  * Read through api.* views and functions only; write only by submitting change sets.
  * Start a run before working and finish it after; every change set names its run.
  * Actuators never submit claims; what they observe comes back through an encoder.
  * A verifier never checks claims produced by its own module or agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Optional, Protocol, Sequence, runtime_checkable

JSON = Mapping[str, Any]
EvidenceType = Literal["deterministic", "extracted", "inferred", "observed", "testimony"]
Lane = Literal["auto", "review"]
HookEvent = Literal["session_start", "before_tool", "after_tool", "before_compact", "stop"]


# ---------------------------------------------------------------- wire types (mirror kernel/schemas/change_set.schema.json)
@dataclass
class Reference:
    source_uri: Optional[str] = None
    artifact: Optional[str] = None          # sha256 of a registered artifact
    quote: Optional[str] = None             # required when evidence_type == "extracted"
    quote_prefix: Optional[str] = None
    quote_suffix: Optional[str] = None
    span: Optional[JSON] = None             # Web Annotation selector, e.g. {"start": 10, "end": 42}


@dataclass
class Op:
    """One change-set operation. `body` holds the op-specific fields from the JSON Schema."""
    op: Literal["add_entity", "add_artifact", "add_claim", "set_status", "add_belief", "add_verification"]
    body: JSON = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"op": self.op, **self.body}


@dataclass
class ChangeSet:
    idempotency_key: str
    attributed_to: str                      # agent IRI mapped to the submitting role
    generated_by: str                       # run id
    target_scope: str
    ops: list[Op]
    lane: Lane = "auto"

    def to_json(self) -> dict:
        return {
            "idempotency_key": self.idempotency_key,
            "attributed_to": self.attributed_to,
            "generated_by": self.generated_by,
            "target_scope": self.target_scope,
            "lane": self.lane,
            "ops": [o.to_json() for o in self.ops],
        }


@dataclass
class Submitted:
    id: str
    status: str
    lane: Lane


# ---------------------------------------------------------------- the kernel, as seen by a module
@runtime_checkable
class KernelClient(Protocol):
    """Thin client over PostgREST using the module's own JWT (its database role)."""

    def start_run(self, module: str, plan_step: Optional[str] = None,
                  used: Sequence[str] = (), info: Optional[JSON] = None) -> str: ...
    def finish_run(self, run_id: str, status: Literal["succeeded", "failed", "cancelled"],
                   cost: Optional[JSON] = None) -> None: ...
    def submit(self, change_set: ChangeSet) -> Submitted: ...
    def claims(self, **filters: str) -> list[JSON]: ...          # GET /claims?<postgrest filters>
    def explain(self, claim_id: str) -> JSON: ...


# ---------------------------------------------------------------- module kinds
@runtime_checkable
class Encoder(Protocol):
    """Source -> change sets. Deterministic parsers set evidence_type 'deterministic';
    model extraction sets 'extracted' and must include quotes."""
    module: str
    def encode(self, source: str, run_id: str) -> Iterable[ChangeSet]: ...


@runtime_checkable
class Processor(Protocol):
    """Claims or events -> change sets (belief, rules, verification, conformance, ...)."""
    module: str
    def accepts(self, event: JSON) -> bool: ...
    def process(self, event: JSON, run_id: str) -> Iterable[ChangeSet]: ...


@dataclass
class DecodeResult:
    media_type: str
    content: bytes
    unrepresented: list[str] = field(default_factory=list)   # claim ids or terms the target format cannot express


@runtime_checkable
class Decoder(Protocol):
    """Shaped claims (+ mappings) -> artifact in a target standard. Must report losses."""
    module: str
    target_format: str
    def decode(self, selection: JSON) -> DecodeResult: ...


@dataclass
class ActuationResult:
    external_refs: list[str]               # ids in the external runtime (workflow instance, job, container)
    accepted: bool
    detail: str = ""


@runtime_checkable
class Actuator(Protocol):
    """Artifact -> effect in an external runtime. Never submits claims."""
    module: str
    allowed_actions: frozenset[str]
    def actuate(self, action: str, artifact: bytes, media_type: str) -> ActuationResult: ...


# ---------------------------------------------------------------- slot-specific contracts
@dataclass
class Belief:
    value: float                            # 0..1
    explanation: JSON


@runtime_checkable
class BeliefModel(Protocol):
    module: str                             # model IRI recorded with every score
    def score(self, claim: JSON, evidence: Sequence[JSON]) -> Belief: ...
    def affected_by(self, changed_claims: Sequence[JSON]) -> list[str]: ...


@dataclass
class Verdict:
    verdict: Literal["supported", "refuted", "inconclusive"]
    details: JSON


@runtime_checkable
class Verifier(Protocol):
    module: str
    applies_to: frozenset[str]              # predicate IRIs or evidence types
    def verify(self, claim: JSON) -> Verdict: ...


@runtime_checkable
class ChainRunner(Protocol):
    """Runs automations in its runner's own native format (no kernel chain format, D-41). One
    kernel run per kernel-writing step, plan_step set to the step IRI. No runner is part of the
    kernel; runner modules are parked (D-36)."""
    module: str
    def load(self, chain: JSON) -> None: ...
    def trigger(self, chain_iri: str, message: JSON) -> str: ...  # returns run id


@dataclass
class HookDecision:
    allow: bool = True
    inject_context: Optional[str] = None
    message: Optional[str] = None


@runtime_checkable
class HarnessAdapter(Protocol):
    """Maps kernel hook events onto one harness's own hook mechanism. Ergonomics only:
    clearance and attribution are enforced at the MCP/API boundary, not here."""
    harness: str
    def install(self, profile: JSON, target_dir: str) -> list[str]: ...   # files written
    def on_event(self, event: HookEvent, payload: JSON) -> HookDecision: ...


@runtime_checkable
class ContextAssembler(Protocol):
    module: str
    def assemble(self, task: str, budget_tokens: int) -> JSON: ...   # claims, entity cards, skills

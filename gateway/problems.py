"""RFC 9457 problem documents: the one place where rejections are shaped.

The kernel rejects with SQLSTATE WMK01 and a JSON detail in kernel vocabulary:
{"problem": <category or check>, "rule": <rule id>, "detail": <text>, ...extras}.
This module maps that to a problem document every harness sees in the same shape:
type, title, status, detail, plus the extension members `rule`, `candidates` and,
where relevant, `nearest`, so a model can usually fix the payload on retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROBLEM_MEDIA_TYPE = "application/problem+json"


@dataclass(frozen=True)
class ProblemType:
    uri: str
    title: str
    status: int


# Rule categories (ontology rules) and write checks, keyed by the kernel's name for them.
PROBLEM_TYPES: dict[str, ProblemType] = {
    "types": ProblemType("urn:wmk:rule:types", "Kind not allowed", 422),
    "domain_range": ProblemType("urn:wmk:rule:domain-range", "Edge not allowed between these nodes", 422),
    "cardinality": ProblemType("urn:wmk:rule:cardinality", "Single-valued edge conflict", 409),
    "time": ProblemType("urn:wmk:rule:time", "Invalid time window", 422),
    "identity": ProblemType("urn:wmk:rule:identity", "Invalid identity key", 422),
    "provenance": ProblemType("urn:wmk:rule:provenance", "Insufficient provenance", 422),
    "governance": ProblemType("urn:wmk:rule:governance", "Not a decision this writer may make", 403),
    "duplicate": ProblemType("urn:wmk:write:duplicate", "Probable duplicate", 409),
    "stale": ProblemType("urn:wmk:write:stale-read", "Stale read", 409),
    "reference": ProblemType("urn:wmk:write:unknown-reference", "Unknown reference", 422),
    "payload": ProblemType("urn:wmk:write:invalid-payload", "Invalid payload", 400),
    "agent": ProblemType("urn:wmk:write:unknown-agent", "Unknown agent", 403),
    "read_only": ProblemType("urn:wmk:read:not-read-only", "Query is not read-only", 400),
    "query": ProblemType("urn:wmk:read:invalid-query", "Invalid query", 400),
    "unavailable": ProblemType("urn:wmk:kernel:unavailable", "Kernel unavailable", 503),
    "internal": ProblemType("urn:wmk:kernel:internal-error", "Internal error", 500),
}

# Members of the kernel detail that are carried into the problem document unchanged.
_EXTENSION_MEMBERS = (
    "nearest",
    "field",
    "op_index",
    "head_offset",
    "changed",
    "conflicting_edges",
    "edge_id",
    "min_basis",
)


class KernelRejection(Exception):
    """Raised by gateway.db when a kernel function rejects (SQLSTATE WMK01)."""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("problem", "rejected")))
        self.detail = detail


def problem(
    kind: str,
    detail: str,
    *,
    rule: str | None = None,
    candidates: list[Any] | None = None,
    instance: str | None = None,
    **extensions: Any,
) -> dict[str, Any]:
    """Build a problem document for one of PROBLEM_TYPES."""
    ptype = PROBLEM_TYPES.get(kind, PROBLEM_TYPES["internal"])
    doc: dict[str, Any] = {
        "type": ptype.uri,
        "title": ptype.title,
        "status": ptype.status,
        "detail": detail,
        "rule": rule,
        "candidates": candidates or [],
    }
    if instance:
        doc["instance"] = instance
    doc.update({k: v for k, v in extensions.items() if v is not None})
    return doc


def from_kernel(detail: dict[str, Any], instance: str | None = None) -> dict[str, Any]:
    """Map a kernel rejection detail to a problem document."""
    extensions = {k: detail[k] for k in _EXTENSION_MEMBERS if k in detail}
    return problem(
        str(detail.get("problem", "internal")),
        str(detail.get("detail", "")),
        rule=detail.get("rule"),
        candidates=detail.get("candidates") or [],
        instance=instance,
        **extensions,
    )


def type_of(doc: dict[str, Any]) -> str:
    return str(doc.get("type", PROBLEM_TYPES["internal"].uri))

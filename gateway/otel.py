"""OpenTelemetry for the gateway. Every span, event, metric and attribute name lives here.

The GenAI and MCP semantic conventions are still in Development status, so names are
kept in one module. The MCP SDK already emits the MCP server span for each request
(with W3C trace context taken from `_meta`); the gateway adds child spans for kernel
calls and links each log entry to its trace.

No personal data: spans, events and metrics carry IDs, offsets, counts and rule names,
never claim text, source content or message content. `safe()` enforces that by
dropping any attribute that is not on the allow-list.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, SpanKind, StatusCode

TRACER_NAME = "wmk.gateway"
METER_NAME = "wmk.gateway"
SERVICE_NAME = "wmk-gateway"

# Span names
SPAN_KERNEL_WRITE = "wmk.kernel.write"
SPAN_KERNEL_INGEST = "wmk.kernel.ingest_source"
SPAN_KERNEL_CITE = "wmk.kernel.cite"
SPAN_KERNEL_READ = "wmk.kernel.read"
SPAN_EMBEDDINGS = "wmk.embeddings"
SPAN_TOOL = "wmk.tool"

# Event names
EVENT_LOG_ENTRY = "wmk.log.entry"
EVENT_REJECTION = "wmk.write.rejected"

# Attribute names
ATTR_TOOL = "gen_ai.tool.name"
ATTR_OPERATION = "gen_ai.operation.name"
ATTR_AGENT_ID = "wmk.agent.id"
ATTR_PROFILE = "wmk.profile"
ATTR_LOG_OFFSET = "wmk.log.offset"
ATTR_ENTRY_ID = "wmk.log.entry_id"
ATTR_CLAIM_ID = "wmk.claim.id"
ATTR_OPS_COUNT = "wmk.ops.count"
ATTR_RESOLUTION = "wmk.claim.resolution"
ATTR_CONFLICTS = "wmk.conflicts.count"
ATTR_RULE = "wmk.rule.id"
ATTR_PROBLEM_TYPE = "wmk.problem.type"
ATTR_SOURCE_ID = "wmk.source.id"
ATTR_SOURCE_SKIPPED = "wmk.source.skipped"
ATTR_CHUNKS = "wmk.chunks.count"
ATTR_ANSWER_ID = "wmk.answer.id"
ATTR_SENTENCES = "wmk.sentences.count"
ATTR_READ = "wmk.read.function"
ATTR_ROWS = "wmk.read.rows"
ATTR_BAND = "wmk.resolution.band"
ATTR_EMBED_COUNT = "wmk.embeddings.count"

ALLOWED_ATTRIBUTES = frozenset(
    {
        ATTR_TOOL,
        ATTR_OPERATION,
        ATTR_AGENT_ID,
        ATTR_PROFILE,
        ATTR_LOG_OFFSET,
        ATTR_ENTRY_ID,
        ATTR_CLAIM_ID,
        ATTR_OPS_COUNT,
        ATTR_RESOLUTION,
        ATTR_CONFLICTS,
        ATTR_RULE,
        ATTR_PROBLEM_TYPE,
        ATTR_SOURCE_ID,
        ATTR_SOURCE_SKIPPED,
        ATTR_CHUNKS,
        ATTR_ANSWER_ID,
        ATTR_SENTENCES,
        ATTR_READ,
        ATTR_ROWS,
        ATTR_BAND,
        ATTR_EMBED_COUNT,
    }
)

# Metric names
METRIC_WRITES = "wmk.writes"
METRIC_REJECTIONS = "wmk.write.rejections"
METRIC_RESOLUTION_BANDS = "wmk.resolution.bands"

_tracer = trace.get_tracer(TRACER_NAME)
_meter = metrics.get_meter(METER_NAME)
_writes = _meter.create_counter(METRIC_WRITES, description="Accepted kernel writes")
_rejections = _meter.create_counter(METRIC_REJECTIONS, description="Rejected writes, by rule")
_bands = _meter.create_counter(METRIC_RESOLUTION_BANDS, description="Resolution candidates, by band")


def safe(attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep only allow-listed attributes with scalar values; drop everything else."""
    return {
        k: v
        for k, v in attributes.items()
        if k in ALLOWED_ATTRIBUTES and v is not None and isinstance(v, str | int | float | bool)
    }


def setup(profile: str) -> None:
    """Install the SDK tracer and meter providers when an OTLP endpoint is configured.

    Without OTEL_EXPORTER_OTLP_ENDPOINT the API stays a no-op and nothing is exported.
    Message content capture stays off: nothing here records payloads.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": SERVICE_NAME, ATTR_PROFILE: profile})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    )


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """A child span for a kernel call. Exceptions mark it failed by type only, never by message."""
    with _tracer.start_as_current_span(
        name,
        kind=SpanKind.CLIENT if name.startswith("wmk.kernel") else SpanKind.INTERNAL,
        attributes=safe(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except Exception as exc:
            current.set_status(StatusCode.ERROR, type(exc).__name__)
            raise


def set_attributes(target: Span, **attributes: Any) -> None:
    target.set_attributes(safe(attributes))


def current_ids() -> tuple[str | None, str | None]:
    """Trace and span id of the current span as hex, or (None, None) without a valid context."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def log_entry_committed(target: Span, offset: int, entry_id: str, claim_id: str, conflicts: int) -> None:
    """After each commit the entry is also an OTel event, so one backend shows world-model changes."""
    target.add_event(
        EVENT_LOG_ENTRY,
        safe(
            {
                ATTR_LOG_OFFSET: offset,
                ATTR_ENTRY_ID: entry_id,
                ATTR_CLAIM_ID: claim_id,
                ATTR_CONFLICTS: conflicts,
            }
        ),
    )
    _writes.add(1)


def rejected(target: Span, problem_type: str, rule: str | None) -> None:
    attrs = safe({ATTR_PROBLEM_TYPE: problem_type, ATTR_RULE: rule or "none"})
    target.add_event(EVENT_REJECTION, attrs)
    target.set_attributes(attrs)
    _rejections.add(1, attrs)


def resolution_bands(bands: list[str]) -> None:
    for band in bands:
        _bands.add(1, {ATTR_BAND: band})

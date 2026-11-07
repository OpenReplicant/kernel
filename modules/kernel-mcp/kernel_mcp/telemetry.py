"""OpenTelemetry for kk serve and other processes that embed the kernel (D-97).

Off unless an OTLP endpoint is configured (OTEL_EXPORTER_OTLP_ENDPOINT or OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
with OTEL_TRACES_EXPORTER not "none") and the SDK is installed (the kk image has it, modules/kernel-mcp/
requirements-otel.txt; kernel-mcp alone does not need it). Then:

- a server span for every HTTP request (ASGI), continuing the caller's trace (W3C traceparent);
- a client span for every Postgres statement (psycopg), carrying db.name, so a service graph shows the database;
- a client span for every outbound HTTP call made with urllib, passing the trace on.

Spans carry the SQL text, never its parameters, and no request or response bodies. Exported over OTLP/HTTP
(protobuf) as the standard OTEL_* variables say. The service name is OTEL_SERVICE_NAME, else the default given.
"""
from __future__ import annotations

import os
import sys

_on = False


def configured() -> bool:
    if os.environ.get("OTEL_TRACES_EXPORTER", "otlp").strip().lower() == "none":
        return False
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def setup(service: str = "kernel") -> bool:
    """Start tracing once per process; True when it is on."""
    global _on
    if _on:
        return True
    if not configured():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        from opentelemetry.instrumentation.urllib import URLLibInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        print(f"OpenTelemetry is configured but not installed ({e.name}); "
              "pip install -r modules/kernel-mcp/requirements-otel.txt", file=sys.stderr)
        return False
    attrs = {} if os.environ.get("OTEL_SERVICE_NAME") else {SERVICE_NAME: service}
    provider = TracerProvider(resource=Resource.create(attrs))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    PsycopgInstrumentor().instrument()
    URLLibInstrumentor().instrument()
    _on = True
    return True


def wrap(app, service: str = "kernel"):
    """The ASGI app with a server span per request when tracing is on; the app itself otherwise."""
    if not setup(service):
        return app
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    return OpenTelemetryMiddleware(app)


def flush(timeout_ms: int = 5000) -> None:
    if _on:
        from opentelemetry import trace
        trace.get_tracer_provider().force_flush(timeout_ms)

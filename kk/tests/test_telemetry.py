"""Kernel tracing (D-97): off without an endpoint; with one, a request through the ASGI app and an outbound call
reach an OTLP/HTTP receiver as spans of service `kernel`, the request continuing the caller's trace."""
import asyncio
import http.server
import os
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules/kernel-mcp"))

received: list[tuple[str, bytes]] = []


class Receiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        received.append((self.path, self.rfile.read(int(self.headers.get("content-length") or 0))))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):  # noqa: N802  (the outbound call under test)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


async def app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def call(asgi, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/mcp", "raw_path": b"/mcp", "query_string": b"", "headers": headers,
             "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000)}
    await asgi(scope, receive, send)
    return sent


def main() -> None:
    from kernel_mcp import telemetry
    checks = 0
    for k in [k for k in os.environ if k.startswith("OTEL_")]:
        del os.environ[k]
    assert telemetry.wrap(app) is app, "tracing must be off without an endpoint"
    checks += 1

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = base
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    wrapped = telemetry.wrap(app)
    assert wrapped is not app, "tracing must wrap the app when an endpoint is set"
    checks += 1
    parent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    sent = asyncio.run(call(wrapped, [(b"traceparent", parent.encode()), (b"host", b"kernel:8000")]))
    assert sent and sent[0]["status"] == 200
    checks += 1
    urllib.request.urlopen(base + "/outbound", timeout=5).close()
    telemetry.flush()
    traces = b"".join(body for path, body in received if path == "/v1/traces")
    assert traces, f"no spans exported: {[p for p, _ in received]}"
    checks += 1
    assert b"kernel" in traces, "the service name is kernel"
    checks += 1
    assert bytes.fromhex("0af7651916cd43dd8448eb211c80319c") in traces, "the request continues the caller's trace"
    checks += 1
    srv.shutdown()
    print(f"kernel tracing: {checks}/6")


if __name__ == "__main__":
    main()

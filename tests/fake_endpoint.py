"""A local model endpoint for tests: speaks the Anthropic Messages API (/v1/messages) and the
OpenAI-compatible chat API (/chat/completions), records every request, and reports fixed usage
(1000 input, 500 output tokens) so costs are predictable."""
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {"input": 1000, "output": 500}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        calls = self.server.calls
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append({"path": self.path, "body": body,
                      "auth": self.headers.get("x-api-key") or self.headers.get("authorization")})
        if self.path.endswith("/v1/messages"):
            out = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                   "content": [{"type": "text", "text": self.server.reply(body, len(calls))}],
                   "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": USAGE["input"], "output_tokens": USAGE["output"]}}
        else:
            out = {"id": "c1", "object": "chat.completion", "model": body["model"],
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "compat reply"}}],
                   "usage": {"prompt_tokens": USAGE["input"], "completion_tokens": USAGE["output"]}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def serve(host: str = "127.0.0.1", reply=None):
    """Yields (base_url, calls). `reply(body, n)` gives the Anthropic reply text for the n-th
    call (default "reply <n>")."""
    server = ThreadingHTTPServer((host, 0), _Handler)
    server.calls = []
    server.reply = reply or (lambda body, n: f"reply {n}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://{host}:{server.server_address[1]}", server.calls
    finally:
        server.shutdown()


def catalog(tag: str, base_url: str) -> str:
    """A catalog with one model per API kind on this endpoint."""
    return f"""
endpoints:
  - {{id: fake-a-{tag}, api: anthropic, base_url: "{base_url}", key_env: FAKE_KEY}}
  - {{id: fake-o-{tag}, api: openai, base_url: "{base_url}/v1"}}
models:
  - {{id: small-a-{tag}, endpoint: fake-a-{tag}, prices: {{input: 1.0, output: 2.0}}, params: {{max_tokens: 64}}}}
  - {{id: small-o-{tag}, endpoint: fake-o-{tag}, prices: {{input: 0.5, output: 1.0}}}}
"""

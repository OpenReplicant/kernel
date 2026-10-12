"""A local model endpoint for tests (ported from the second iteration): speaks the Anthropic
Messages API (/v1/messages) and the OpenAI-compatible chat API (/v1/chat/completions), records
every request, and reports fixed usage (1000 input, 500 output tokens) so costs are predictable.
`server.reply(body)` gives the reply text (set it per test)."""
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {"input": 1000, "output": 500}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append({"path": self.path, "body": body,
                                  "auth": self.headers.get("x-api-key") or self.headers.get("authorization")})
        text = self.server.reply(body)
        if self.path.endswith("/v1/messages"):
            out = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                   "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": USAGE["input"], "output_tokens": USAGE["output"]}}
        else:
            out = {"id": "c1", "object": "chat.completion", "model": body["model"],
                   "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
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
def serve(host: str = "127.0.0.1"):
    """Yields the server: .url, .calls, .reply."""
    server = ThreadingHTTPServer((host, 0), _Handler)
    server.calls = []
    server.reply = lambda body: "ok"
    server.url = f"http://{host}:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()

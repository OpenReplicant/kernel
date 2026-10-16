"""Self-encoder service. Writes artifact bytes to the artifact store; never writes to the kernel
(the scan chain submits the change sets under this module's identity).

  POST /list    {}                                  -> {"paths": [...]}
  POST /encode  {"paths": [...], "blocked": [...]}  -> {"change_sets": [...], "blocked": [...]}

Env: KK_REPO (default /repo), KK_ARTIFACTS (default /artifacts), PORT (default 8000).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .encode import encode_file, list_files

REPO = Path(os.environ.get("KK_REPO", "/repo")).resolve()
ARTIFACTS = Path(os.environ.get("KK_ARTIFACTS", "/artifacts"))


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(200, {"ok": True}) if self.path == "/health" else self._reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(length)) if length else {}
            if self.path == "/list":
                return self._reply(200, {"paths": list_files(REPO)})
            if self.path == "/encode":
                allowed = set(list_files(REPO))
                paths = [p for p in msg["paths"] if p in allowed]
                return self._reply(200, {"change_sets": [encode_file(REPO, p, ARTIFACTS) for p in paths],
                                         "blocked": msg.get("blocked", [])})
            self._reply(404, {"error": "not found"})
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

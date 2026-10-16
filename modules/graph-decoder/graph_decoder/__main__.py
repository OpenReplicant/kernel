"""Graph service: GET /graph?module=|slot=|chain=|root=&depth=&format=cytoscape|mermaid

Reads claims with the *viewer's* bearer token (forwarded), so each person sees exactly what their
clearance allows; without a token the request is refused. Writes nothing.
Env: KK_API, PORT (default 8000).
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .graph import build, cytoscape, mermaid, select

API = os.environ.get("KK_API", "http://postgrest:3000").rstrip("/")
COLUMNS = "id,subject,predicate,object_entity,object_value,qualifiers,status,belief"


def claims(token: str) -> list:
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"select": COLUMNS, "order": "id", "limit": 1000, "offset": offset})
        req = urllib.request.Request(f"{API}/claims?{q}", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/health":
            return self._reply(200, {"ok": True})
        if url.path not in ("/graph", "/graph/"):
            return self._reply(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            return self._reply(401, {"error": "send your own token: Authorization: Bearer <JWT>"})
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            g = build(claims(auth[7:].strip()))
            keep = select(g, q.get("module"), q.get("slot"), q.get("chain"), q.get("root"), int(q.get("depth", 1)))
            if q.get("format") == "mermaid":
                return self._reply(200, mermaid(g, keep), "text/vnd.mermaid; charset=utf-8")
            return self._reply(200, cytoscape(g, keep), "application/vnd.cytoscape+json")
        except urllib.error.HTTPError as e:
            self._reply(e.code, {"error": e.read().decode()[:300]})
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

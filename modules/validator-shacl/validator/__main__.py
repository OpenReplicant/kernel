"""Validator service: POST /validate takes one change set (a row of api.validation_queue) and returns
{"ok": bool, "report": {...}}. Recording the verdict (api.record_validation) is the job of the
validate-one chain, so this service never writes.

Env: KK_API (PostgREST base URL), KK_JWT (this module's token, used to read context claims), PORT.
"""
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import VERSION, entity_objects, validate

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def fetch_context(cs: dict) -> list:
    """rdf:type claims (not deprecated) of the entities the change set points at."""
    iris = sorted(entity_objects(cs))
    if not iris or not os.environ.get("KK_API"):
        return []
    quoted = ",".join('"' + i.replace('"', '') + '"' for i in iris)
    query = urllib.parse.urlencode({"predicate": f"eq.{RDF_TYPE}", "subject": f"in.({quoted})",
                                    "status": "neq.deprecated", "select": "subject,object_entity"})
    req = urllib.request.Request(f"{os.environ['KK_API']}/claims?{query}",
                                 headers={"Authorization": f"Bearer {os.environ.get('KK_JWT', '')}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.load(r)
    return [(row["subject"], RDF_TYPE, row["object_entity"]) for row in rows if row["object_entity"]]


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, {"ok": True, "version": VERSION})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/validate":
            return self._reply(404, {"error": "not found"})
        try:
            cs = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            ok, report = validate(cs, fetch_context(cs))
            self._reply(200, {"id": cs.get("id"), "ok": ok, "report": report})
        except Exception as e:  # report, don't crash the service
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

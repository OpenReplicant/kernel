"""Belief service: POST /rescore returns change sets of add_belief ops (one per scope) for claims whose
belief changed. It reads through the API with its own token and never writes; the rescore chain
submits under this module's identity.

Each call looks at claims recorded since the last call (all claims on the first call and on every
FULL_EVERY-th call, which also catches commits that landed out of order), widens to every claim on
the same subjects, and scores them. Env: KK_API, KK_JWT, PORT (default 8000), FULL_EVERY (default 60).
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .model import MODEL, Claim, score

API = os.environ.get("KK_API", "http://postgrest:3000")
AGENT = "urn:kk:agent:belief-corroboration"
FUNCTIONAL_TYPE = "http://www.w3.org/2002/07/owl#FunctionalProperty"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
COLUMNS = "id,subject,predicate,object_entity,object_value,statement_key,scope,attributed_to,valid_from,valid_to,status,belief,recorded_at"
state = {"watermark": None, "calls": 0}


def get(path: str, **params) -> list:
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({**params, "limit": 1000, "offset": offset})
        req = urllib.request.Request(f"{API}/{path}?{q}", headers={"Authorization": f"Bearer {os.environ['KK_JWT']}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def in_list(values) -> str:
    return "in.(" + ",".join('"' + v.replace('"', "") + '"' for v in values) + ")"


def chunks(items, n=50):
    items = sorted(items)
    return (items[i:i + n] for i in range(0, len(items), n))


def when(v):
    return datetime.fromisoformat(v) if v else None


def rescore() -> dict:
    state["calls"] += 1
    full = state["watermark"] is None or state["calls"] % int(os.environ.get("FULL_EVERY", "60")) == 0
    new = get("claims", select="subject,recorded_at", order="recorded_at",
              **({} if full else {"recorded_at": f"gt.{state['watermark']}"}))
    if not new:
        return {"change_sets": []}
    watermark = max(r["recorded_at"] for r in new)
    rows = [r for part in chunks({r["subject"] for r in new}) for r in get("claims", select=COLUMNS, subject=in_list(part))]
    sources: dict[str, set] = {}
    for part in chunks({r["id"] for r in rows}):
        for ref in get("claim_references", select="claim_id,artifact,source_uri", claim_id=in_list(part)):
            sources.setdefault(ref["claim_id"], set()).add(ref["artifact"] or ref["source_uri"])
    functional = {r["subject"] for r in get("claims", select="subject", predicate=f"eq.{RDF_TYPE}",
                                             object_entity=f"eq.{FUNCTIONAL_TYPE}", status="neq.deprecated")}
    claims = [Claim(r["id"], r["statement_key"], r["subject"], r["predicate"],
                    json.dumps(r["object_entity"] or r["object_value"], sort_keys=True), r["attributed_to"],
                    frozenset(sources.get(r["id"], ())), when(r["valid_from"]), when(r["valid_to"]),
                    r["status"] == "deprecated") for r in rows]
    beliefs = score(claims, functional)
    ops_by_scope: dict[str, list] = {}
    for r in rows:
        if r["id"] not in beliefs:
            continue
        value, explanation = beliefs[r["id"]]
        if r["belief"] is None or abs(r["belief"] - value) > 1e-4:
            ops_by_scope.setdefault(r["scope"], []).append(
                {"op": "add_belief", "claim": r["id"], "model": MODEL, "value": value, "explanation": explanation})
    change_sets = []
    for scope, ops in sorted(ops_by_scope.items()):
        digest = hashlib.sha256(json.dumps(ops, sort_keys=True).encode()).hexdigest()[:24]
        change_sets.append({"idempotency_key": f"belief:{digest}", "attributed_to": AGENT,
                            "target_scope": scope, "ops": ops})
    state["watermark"] = watermark
    return {"change_sets": change_sets}


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
        if self.path != "/rescore":
            return self._reply(404, {"error": "not found"})
        try:
            self._reply(200, rescore())
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):                     # polled every few seconds: keep logs quiet
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

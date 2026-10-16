"""Chain decoder service. The only writer of the RuleGo chain folder; never writes to the kernel
itself (its chains submit the change sets it returns, under its own identity).

  POST /deploy            compile approved chain claims; write a file when a chain's compiled output
                          changes; return claims kk:runnable, kk:notRunnableBecause, kk:deployedHash
  POST /deploy?restore=1  also rewrite files whose content drifted from what was deployed
  POST /drift             hash the deployed files; return kk:observedHash claims when a file differs
                          from its kk:deployedHash (and when such drift is resolved)

Values change by supersession (the old claim is deprecated, replaced_by the new one), never by edit.
Env: KK_API, KK_JWT (this module's token), KK_RULEGO_DIR (default /rulego/workflows/kk/rules), PORT.
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .compile import KK, chains_from_claims, compile_all, render, slug

API = os.environ.get("KK_API", "http://postgrest:3000").rstrip("/")
OUT = Path(os.environ.get("KK_RULEGO_DIR", "/rulego/workflows/kk/rules"))
AGENT = "urn:kk:agent:chain-decoder-rulego"
SCOPE = "urn:kk:scope:default"
COLUMNS = "id,subject,predicate,object_entity,object_value,qualifiers,status,attributed_to"


def claims() -> list:
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"select": COLUMNS, "status": "neq.deprecated", "order": "id",
                                    "limit": 1000, "offset": offset})
        req = urllib.request.Request(f"{API}/claims?{q}", headers={"Authorization": f"Bearer {os.environ['KK_JWT']}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def current(rows, predicate) -> dict:
    """The decoder's own live claims for a predicate, by chain: {chain: (claim id, value)}."""
    return {r["subject"]: (r["id"], (r["object_value"] or {}).get("@value")) for r in rows
            if r["predicate"] == KK + predicate and r["attributed_to"] == AGENT}


class Ops:
    def __init__(self):
        self.ops, self.n = [], 0

    def set(self, chain, predicate, value, old, derived=None, source=None, evidence="deterministic"):
        """Make `value` the live value of chain/predicate (None: retract), superseding `old`."""
        if old and old[1] == value:
            return
        ref = None
        if value is not None:
            self.n += 1
            ref = f"c{self.n}"
            op = {"op": "add_claim", "ref": ref, "subject": chain, "predicate": f"kk:{predicate}",
                  "object": {"value": {"@value": value, **({"@type": "xsd:boolean"} if isinstance(value, bool) else {})}},
                  "evidence_type": evidence}
            if derived:
                op["derived_from"] = derived
            if source:
                op["references"] = [{"source_uri": source}]
            self.ops.append(op)
        if old:
            self.ops.append({"op": "set_status", "claim": old[0], "status": "deprecated", "expect_status": "normal",
                             **({"reason": "superseded", "replaced_by": ref} if ref else {"reason": "retracted"})})

    def change_sets(self, kind):
        if not self.ops:
            return []
        digest = hashlib.sha256(json.dumps(self.ops, sort_keys=True).encode()).hexdigest()[:24]
        return [{"idempotency_key": f"chain-decoder-rulego:{kind}:{digest}", "attributed_to": AGENT,
                 "target_scope": SCOPE, "ops": self.ops}]


def deploy(restore: bool) -> dict:
    rows = claims()
    chains = chains_from_claims(rows)
    module_of = {}
    agents = {r["subject"]: r["object_entity"] for r in rows if r["predicate"] == KK + "runsAs"}
    modules = {r["subject"] for r in rows if r["predicate"].endswith("#type") and r["object_entity"] == KK + "Module"}
    for iri, c in chains.items():
        owners = sorted(m for m in modules if agents.get(m) == c.get("runs_as"))
        module_of[iri] = owners[0] if owners else ""
    results = compile_all(chains, module_of)
    runnable, reasons, deployed = current(rows, "runnable"), current(rows, "notRunnableBecause"), current(rows, "deployedHash")
    ops, written = Ops(), []
    OUT.mkdir(parents=True, exist_ok=True)
    for iri, (rule_chain, why) in sorted(results.items()):
        derived = chains[iri]["claims"]
        path = OUT / f"{slug(iri)}.json"
        ops.set(iri, "runnable", rule_chain is not None, runnable.get(iri), derived=derived)
        ops.set(iri, "notRunnableBecause", "; ".join(why) if why else None, reasons.get(iri), derived=derived)
        if rule_chain is None:
            continue
        data = render(rule_chain)
        digest = hashlib.sha256(data).hexdigest()
        on_disk = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        changed = deployed.get(iri, (None, None))[1] != digest
        if changed or on_disk is None or (restore and on_disk != digest):
            path.write_bytes(data)
            written.append(path.name)
        ops.set(iri, "deployedHash", digest, deployed.get(iri), derived=derived,
                source=f"rulego:kk/rules/{path.name}#sha256={digest}")
    return {"change_sets": ops.change_sets("deploy"), "written": written,
            "runnable": sorted(slug(i) for i, r in results.items() if r[0] is not None),
            "not_runnable": {slug(i): r[1] for i, r in sorted(results.items()) if r[0] is None}}


def drift() -> dict:
    rows = claims()
    deployed, observed = current(rows, "deployedHash"), current(rows, "observedHash")
    ops, drifted = Ops(), []
    for iri, (claim_id, digest) in sorted(deployed.items()):
        path = OUT / f"{slug(iri)}.json"
        seen = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
        if seen != digest:
            drifted.append(slug(iri))
        if seen != digest or iri in observed:
            ops.set(iri, "observedHash", seen, observed.get(iri), derived=[claim_id],
                    source=f"rulego:kk/rules/{path.name}", evidence="observed")
    return {"change_sets": ops.change_sets("drift"), "drifted": drifted}


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
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/deploy":
                return self._reply(200, deploy("restore=1" in url.query))
            if url.path == "/drift":
                return self._reply(200, drift())
            self._reply(404, {"error": "not found"})
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

"""Runner service: loads modules/*/chains/*.yaml, runs schedule triggers, and accepts manual triggers.

Env:
  KK_API             PostgREST base URL
  KK_AGENT_TOKENS    JSON {agent IRI: JWT} for the runs_as agents this runner may act as
  KK_MODULES         modules folder (default /modules); KK_ROOT repository root for the chain schema
  PORT               HTTP port for POST /trigger?chain=<IRI> with a JSON message body (default 8000)
Triggers not handled here (http, notify, mcp) are logged at start; chain triggers run as subchains.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonschema
import yaml

from .engine import Chain, Runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chain_runner")


def http(method, url, token, body):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")[:500]
    except OSError as e:
        return 599, f"{type(e).__name__}: {e}"


def load_chains(modules: Path, schema: dict) -> dict:
    chains = {}
    for path in sorted(modules.glob("*/chains/*.yaml")):
        if path.parent.parent.name.startswith("_"):
            continue
        d = yaml.safe_load(path.read_text())
        jsonschema.validate(d, schema)
        chains[d["chain"]] = Chain.from_dict(d, path.parent.parent)
    return chains


def every(spec: str) -> float:
    m = re.fullmatch(r"every (\d+(?:\.\d+)?)(ms|s|m)", spec.strip())
    if not m:
        raise ValueError(f"unsupported schedule {spec!r}")
    return float(m.group(1)) * {"ms": 0.001, "s": 1, "m": 60}[m.group(2)]


def report(ex) -> None:
    if ex.runs or not ex.ok:
        log.info("%s ok=%s runs=%d trace=%s", ex.chain, ex.ok, len(ex.runs), json.dumps(ex.trace))


def schedule_loop(runner: Runner, iri: str, interval: float) -> None:
    while True:
        started = time.monotonic()
        try:
            report(runner.execute(iri))
        except Exception:                               # keep the loop alive
            log.exception("%s crashed", iri)
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def main() -> None:
    root = Path(os.environ.get("KK_ROOT", "/kk"))
    schema = json.loads((root / "kernel/schemas/chain.schema.json").read_text())
    chains = load_chains(Path(os.environ.get("KK_MODULES", "/modules")), schema)
    tokens = json.loads(os.environ.get("KK_AGENT_TOKENS") or "{}")
    runner = Runner(chains, tokens, os.environ["KK_API"], http)

    for iri, chain in chains.items():
        t = chain.trigger
        if chain.runs_as not in tokens:
            log.info("loaded %s (inactive: no token for %s)", iri, chain.runs_as)
        elif t["type"] == "schedule":
            threading.Thread(target=schedule_loop, args=(runner, iri, every(t["spec"])), daemon=True).start()
            log.info("loaded %s (schedule %s)", iri, t["spec"])
        else:
            log.info("loaded %s (trigger %s: manual or subchain only)", iri, t["type"])

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, {"ok": True, "chains": sorted(chains)})
            self._reply(404, {"error": "not found"})

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            iri = urllib.parse.parse_qs(url.query).get("chain", [""])[0]
            if url.path != "/trigger" or iri not in chains:
                return self._reply(404, {"error": "unknown chain"})
            length = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(length)) if length else None
            ex = runner.execute(iri, msg)
            report(ex)
            self._reply(200, {"ok": ex.ok, "msg": ex.msg, "trace": ex.trace, "runs": ex.runs})

    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()


if __name__ == "__main__":
    main()

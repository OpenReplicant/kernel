"""POST /filter {"paths": [relative paths under KK_REPO]} -> {"paths": [clean], "blocked": [...]}.

Blocked entries name the file, line and gitleaks rule, never the secret. Anything else in the request
is passed through, so the filter can sit between a lister and an encoder in a chain.
Env: KK_REPO (default /repo), PORT (default 8000).
"""
import json
import os
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("KK_REPO", "/repo")).resolve()


def scan(paths: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        for rel in paths:
            src = (REPO / rel).resolve()
            if REPO not in src.parents:
                raise ValueError(f"path outside the repository: {rel}")
            dst = Path(tmp) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        report = Path(tmp) / ".gitleaks-report.json"
        proc = subprocess.run(["gitleaks", "dir", tmp, "--no-banner", "--redact", "--exit-code", "0",
                               "-f", "json", "-r", str(report)], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"gitleaks failed: {proc.stderr[-300:]}")
        findings = json.loads(report.read_text() or "[]")
    blocked = sorted({(str(Path(f["File"]).relative_to(tmp)), f["StartLine"], f["RuleID"]) for f in findings})
    bad = {b[0] for b in blocked}
    return {"paths": [p for p in paths if p not in bad],
            "blocked": [{"path": p, "line": line, "rule": rule} for p, line, rule in blocked]}


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
        if self.path != "/filter":
            return self._reply(404, {"error": "not found"})
        try:
            msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self._reply(200, {**msg, **scan(msg["paths"])})
        except Exception as e:
            self._reply(400, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()

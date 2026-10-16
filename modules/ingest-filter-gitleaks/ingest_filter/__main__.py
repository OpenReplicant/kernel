"""Ingest filter: gitleaks over candidate files before anything is stored. A plain command (D-41):

  python -m ingest_filter --repo DIR PATH...     (or the paths as a JSON list on stdin)
  -> stdout {"paths": [clean], "blocked": [{"path", "line", "rule"}]}

Blocked entries name the file, line and gitleaks rule, never the secret. Any encoder can run it
before encoding (the self-encoder does, through KK_INGEST_FILTER). Needs the gitleaks binary on PATH
(pinned: v8.30.1). Fails closed: if gitleaks cannot run, the command exits non-zero.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def scan(repo: Path, paths: list[str]) -> dict:
    repo = repo.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        for rel in paths:
            src = (repo / rel).resolve()
            if repo not in src.parents:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()
    paths = a.paths or json.load(sys.stdin)
    print(json.dumps(scan(Path(a.repo), paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

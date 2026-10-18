"""Ingest filter: gitleaks over candidate files before anything is stored. A plain command (D-41):

  python -m ingest_filter --repo DIR PATH...     (or the paths as a JSON list on stdin)
  -> stdout {"paths": [clean], "blocked": [{"path", "line", "rule"}]}

Blocked entries name the file, line and gitleaks rule, never the secret. Any encoder can run it
before encoding (the self-encoder does, through KK_INGEST_FILTER). Needs the gitleaks binary on PATH
(pinned: v8.30.1). Fails closed: if gitleaks cannot run, the command exits non-zero.

gitleaks scans on one core, so a file over CHUNK bytes is copied as overlapping chunks, shared out over
one gitleaks process per CPU; a finding's line is mapped back to the file's (M3-T4: a 728 MB event log
on a single line). The
time limit grows with the bytes scanned (KK_FILTER_SECONDS_PER_MB, default 2, at least 300 s).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


CHUNK = 32 << 20     # bytes
OVERLAP = 64 << 10   # each chunk also holds the next OVERLAP bytes, so a secret across a cut is whole in one


def split(src: Path, dst: Path) -> dict[str, tuple[str, int]]:
    """Copy `src` as chunks `<stem>.~kkchunkN<suffix>` (the suffix kept for path rules) of CHUNK bytes
    plus OVERLAP, cut at byte offsets (a log may be a single line): {chunk name: (dst name, the
    newlines before its first byte)}, so a finding's line in a chunk maps back to the file's."""
    chunks, n, lines = {}, 0, 0
    with open(src, "rb") as f:
        while True:
            f.seek(n * CHUNK)
            data = f.read(CHUNK + OVERLAP)
            if not data:
                break
            name = f"{dst.stem}.~kkchunk{n}{dst.suffix}"
            (dst.parent / name).write_bytes(data)
            chunks[name] = (dst.name, lines)
            lines += data[:CHUNK].count(b"\n")
            n += 1
            if len(data) <= CHUNK:
                break
    return chunks


def scan(repo: Path, paths: list[str]) -> dict:
    repo = repo.resolve()
    shards = max(1, os.cpu_count() or 1)
    with tempfile.TemporaryDirectory() as tmp:
        parts, total, n = {}, 0, 0    # (shard, path in it) -> (file path, line offset)
        for rel in paths:
            src = (repo / rel).resolve()
            if repo not in src.parents:
                raise ValueError(f"path outside the repository: {rel}")
            total += src.stat().st_size
            if src.stat().st_size <= CHUNK:
                dst = Path(tmp, "0", rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                continue
            staged = Path(tmp, "split", rel)
            staged.parent.mkdir(parents=True, exist_ok=True)
            for name, (_, offset) in split(src, staged).items():   # chunks round-robin over the shards
                k, n = str(n % shards), n + 1
                to = Path(tmp, k, rel).parent / name
                to.parent.mkdir(parents=True, exist_ok=True)
                (staged.parent / name).rename(to)
                parts[(k, str(to.relative_to(Path(tmp, k))))] = (rel, offset)
        limit = max(300, total / (1 << 20) * float(os.environ.get("KK_FILTER_SECONDS_PER_MB", "2")))
        dirs = sorted(d.name for d in Path(tmp).iterdir() if d.name != "split")
        procs = {k: subprocess.Popen(["gitleaks", "dir", str(Path(tmp, k)), "--no-banner", "--redact", "--exit-code", "0",
                                      "-f", "json", "-r", str(Path(tmp, f"report-{k}.json"))],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) for k in dirs}
        findings = []
        try:
            for k, proc in procs.items():
                _, err = proc.communicate(timeout=limit)
                if proc.returncode != 0:
                    raise RuntimeError(f"gitleaks failed: {err[-300:]}")
                for f in json.loads(Path(tmp, f"report-{k}.json").read_text() or "[]"):
                    rel = str(Path(f["File"]).relative_to(Path(tmp, k)))
                    path, offset = parts.get((k, rel), (rel, 0))
                    findings.append((path, f["StartLine"] + offset, f["RuleID"]))
        finally:
            for proc in procs.values():
                proc.kill()
    blocked = sorted(set(findings))
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

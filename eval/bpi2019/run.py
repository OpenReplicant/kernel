"""A public event log at scale (M3-T4, D-72): BPI Challenge 2019 through the event-log encoder and the
conformance processor, timed, with each step's peak memory.

  python eval/bpi2019/run.py fetch               download the log (728 MB XES), checked by sha256
  python eval/bpi2019/run.py scale [--window month] [--no-filter]
                                                 ingest filter, encode, validate and apply, the documented
                                                 model (documented.bpmn), one conformance pass; results
                                                 to eval/bpi2019/runs/<date>-scale-<window>.json

The data: van Dongen, B.F. (2019) BPI Challenge 2019. 4TU.ResearchData. CC BY 4.0.
https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 — downloaded at run time, not committed.

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_eventlog, kkm_encoder_bpmn and kkm_conformance)
with `kk serve` running the validator on the same kernel; gitleaks on PATH (the ingest filter);
KK_EVAL_CACHE (default ~/.cache/knowledge-kernel/eval); KK_ARTIFACTS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("KK_EVAL_CACHE") or Path.home() / ".cache/knowledge-kernel/eval") / "bpi2019"
LOG = CACHE / "BPI_Challenge_2019.xes"
URL = "https://data.4tu.nl/file/35ed7122-966a-484e-a0e1-749b64e3366d/864493d1-3a58-47f6-ad6f-27f95f995828"
SHA256 = "af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"   # pinned Oct 2026 (md5 as published: 4eb909242351193a61e1c15b9c3cc814)
PROCESS = "urn:kk:process:bpi2019"
# Runs a module as __main__ and reports its peak memory on stderr (one JSON line).
WRAP = ("import json, resource, runpy, sys\nsys.argv = sys.argv[1:]\ntry:\n    runpy.run_module(sys.argv[0], run_name='__main__')\n"
        "except SystemExit as e:\n    code = e.code\nelse:\n    code = 0\n"
        "print(json.dumps({'peak_rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}), file=sys.stderr)\n"
        "sys.exit(code or 0)")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not LOG.exists() or sha256(LOG) != SHA256:
        part = LOG.with_suffix(".part")
        with urllib.request.urlopen(URL, timeout=600) as r, open(part, "wb") as f:
            while block := r.read(1 << 20):
                f.write(block)
        if sha256(part) != SHA256:
            sys.exit(f"{part}: sha256 does not match the pinned one")
        part.rename(LOG)
    print(f"{LOG} ({LOG.stat().st_size} bytes, sha256 ok)")


def step(module: str, *args: str, role: str) -> tuple[dict, float, int]:
    """Run a module as its role: (its last JSON line, seconds, peak MB)."""
    started = time.monotonic()
    p = subprocess.run([sys.executable, "-c", WRAP, module, *args], env={**os.environ, "KK_ROLE": role},
                       capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"{module} failed: {p.stderr[-2000:]}")
    peak = json.loads(p.stderr.strip().splitlines()[-1])["peak_rss_mb"]
    return json.loads(p.stdout.strip().splitlines()[-1]), time.monotonic() - started, peak


def settle(role: str, ids: list[str], limit: float = 7200) -> tuple[dict, float]:
    """Wait until the validator has decided these change sets: their statuses and the seconds waited."""
    import psycopg
    started = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
        db.execute(f"set role {role}")
        while True:
            rows = db.execute("select status, count(*) from api.change_sets where id = any(%s::uuid[]) group by 1",
                              (ids,)).fetchall()
            if "submitted" not in dict(rows) or time.monotonic() - started > limit:
                return dict(rows), time.monotonic() - started
            time.sleep(2)


def scale(window: str, use_filter: bool) -> dict:
    if not LOG.exists():
        sys.exit("no log: run `python eval/bpi2019/run.py fetch` first")
    started = time.monotonic()
    enc, enc_s, enc_peak = step("eventlog_encoder", "--process", PROCESS, "--window", window,
                                *([] if use_filter else ["--no-filter"]), str(LOG), role="kkm_encoder_eventlog")
    enc_status, enc_wait = settle("kkm_encoder_eventlog", enc["change_sets"])
    doc, _, _ = step("bpmn_encoder", "--process", PROCESS, str(HERE / "documented.bpmn"), role="kkm_encoder_bpmn")
    settle("kkm_encoder_bpmn", [doc["change_set"]])
    conf, conf_s, conf_peak = step("conformance", "--once", role="kkm_conformance")
    ids = list(conf["discrepancies"].values())
    conf_status, conf_wait = settle("kkm_conformance", ids) if ids else ({}, 0.0)
    kinds, top = Counter(), []
    if ids:   # what the pass found, by kind and size: the same comparison, read-only, as the module's role
        import psycopg
        from conformance.__main__ import CLAIMS, CONTAINER, LABEL, MAX, NEXT, RESULT, discrepancies
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
            db.execute("set role kkm_conformance")
            cur = db.execute(CLAIMS, {"p": [NEXT, CONTAINER, RESULT, MAX], "label": LABEL, "c": CONTAINER})
            cols = [d.name for d in cur.description]
            for d in discrepancies([dict(zip(cols, r)) for r in cur.fetchall()]):
                kinds[d["kind"].rsplit(":", 1)[-1]] += 1
                n = re.match(r"(\d+) case", d["text"])
                top.append((int(n.group(1)) if n else 0, d["text"]))
    return {
        "stage": "scale", "log": LOG.name, "sha256": SHA256, "window": window, "ingest_filter": use_filter,
        "events": enc["events"], "claims": enc["claims"], "change_sets": len(enc["change_sets"]),
        "encoder": {"seconds": round(enc_s, 1), "stages": enc["seconds"], "peak_rss_mb": enc_peak},
        "validate_and_apply": {"seconds": round(enc_wait, 1), "status": enc_status},
        "conformance": {"seconds": round(conf_s, 1), "peak_rss_mb": conf_peak, "discrepancies": len(ids),
                        "by_kind": dict(kinds.most_common()), "validate_seconds": round(conf_wait, 1),
                        "status": conf_status, "largest": [t for _, t in sorted(top, reverse=True)[:8]]},
        "seconds_total": round(time.monotonic() - started, 1),
        "machine": {"cpus": os.cpu_count()},
        "data": "van Dongen (2019) BPI Challenge 2019, 4TU.ResearchData, CC BY 4.0, doi:10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="BPI Challenge 2019 at scale (M3-T4)")
    ap.add_argument("stage", choices=("fetch", "scale"))
    ap.add_argument("--window", default="month", choices=("all", "day", "week", "month"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter")
    a = ap.parse_args()
    if a.stage == "fetch":
        fetch()
    else:
        result = scale(a.window, not a.no_filter)
        out = HERE / "runs" / f"{date.today().isoformat()}-scale-{a.window}{'-nofilter' if a.no_filter else ''}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))

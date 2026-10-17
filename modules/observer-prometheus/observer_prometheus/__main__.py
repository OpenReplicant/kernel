"""Metrics observer: asks Prometheus for the queries in queries.yaml once per window and records the
answers as observations of the compose services (D-52). Needs no runner.

  python -m observer_prometheus [--once]

Each window (KK_WINDOW_SECONDS, default 60, aligned to the clock) gives one change set, keyed by the
Prometheus URL and the window end, so a restart never records a window twice. It reaches Prometheus
over the kk-reach-prometheus network only (manifest network.reach).
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_observer_prometheus), PROMETHEUS_URL (default http://prometheus:9090), KK_WINDOW_SECONDS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import yaml

from kk.lib import connect, run, submit

from .observe import ops, query_url, window

MODULE = "urn:kk:module:observer-prometheus"
AGENT = "urn:kk:agent:observer-prometheus"
SCOPE = "urn:kk:scope:default"
QUERIES = Path(__file__).resolve().parents[1] / "queries.yaml"


def observe(conn, base: str, seconds: int, queries: list[dict]) -> dict:
    start, end = window(time.time(), seconds)
    results = []
    for q in queries:
        url = query_url(base, q["query"].replace("{w}", str(seconds)), end)
        with urllib.request.urlopen(url, timeout=10) as r:
            results.append((q["property"], url, json.load(r)))
    found = ops(results, start, end)
    if not found:
        return {"window_end": end, "observations": 0}
    cs = {"idempotency_key": f"observer:{base}|{end:.0f}", "attributed_to": AGENT, "target_scope": SCOPE, "ops": found}
    with conn.cursor() as cur, run(cur, MODULE, {"window_end": end}) as (run_id, outputs):
        res = submit(cur, cs, run_id)
        outputs.update(observations=len(found))
    return {"window_end": end, "observations": len(found), "change_set": str(res["id"]), "status": res["status"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record Prometheus metrics as observations, once per window.")
    ap.add_argument("--once", action="store_true", help="observe the last complete window and exit")
    a = ap.parse_args(argv)
    base = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
    seconds = int(os.environ.get("KK_WINDOW_SECONDS", "60"))
    queries = yaml.safe_load(QUERIES.read_text())
    conn = connect(os.environ.get("KK_ROLE", "kkm_observer_prometheus"))
    while True:
        try:
            print(json.dumps(observe(conn, base, seconds, queries)), flush=True)
        except OSError as e:          # Prometheus unreachable: say so and try the next window
            if a.once:
                raise
            print(json.dumps({"error": str(e)}), file=sys.stderr, flush=True)
        if a.once:
            return 0
        time.sleep(seconds - time.time() % seconds + 1)


if __name__ == "__main__":
    sys.exit(main())

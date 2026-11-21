"""Detector worker: runs the registered detectors from a cursor over what the kernel applied.

  python -m detectors [--once] [--file detectors.yaml]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE and to each detector's owner),
KK_ROLE (default kkm_detectors), KK_DETECTORS_FILE, KK_SWEEP_SECONDS, KK_DETECTORS_FULL_EVERY (every nth pass
is a full look, default 20), KK_DETECTORS_BATCH (change sets per pass, default 1000), KK_DETECTORS_TIMEOUT_MS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from kk.lib import connect, listen

from .engine import Engine
from .registry import load

MODULE, AGENT = "urn:kk:module:detectors", "urn:kk:agent:detectors"


def worker(detectors, role: str = "kkm_detectors", agent: str = AGENT, module: str = MODULE, once: bool = False) -> int:
    engine = Engine(detectors, role, agent, module)
    conn = connect(role)
    if once:
        print(json.dumps(engine.run_pass(conn), default=str))
        return 0
    listen(conn, engine.run_pass, wake=lambda p: json.loads(p).get("status") == "applied")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the registered detectors from a cursor over applied change sets.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--file", default=os.environ.get("KK_DETECTORS_FILE"))
    a = ap.parse_args(argv)
    detectors = load(a.file) if a.file else []
    if not detectors:
        print("detectors: none registered (KK_DETECTORS_FILE); idle", file=sys.stderr)
    return worker(detectors, os.environ.get("KK_ROLE", "kkm_detectors"), once=a.once)


if __name__ == "__main__":
    sys.exit(main())

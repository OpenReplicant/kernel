"""kk demo [--bpi]: a customer-ready process demo in one command (M4-T4, D-77).

1. `kk up` on a demo project folder (default ~/kk-demo): the compose stack (with the process loop:
   conformance and the process learner), the owner's sign-in and the demo agent's login, the harness
   files. With --no-stack, the kernel at KK_ADMIN_URL as it is (run `kk serve --only ...,conformance,learner`).
2. The process, as the modules record it, each as its own role: the event log (event-log encoder), the
   documented model (BPMN encoder) and, for order-to-cash, the written procedure's ordering and time
   limit with their quote (the demo agent, as the process-extraction skill has it). Order-to-cash is
   bundled (40 cases, offline); --bpi fetches BPI Challenge 2019 (CC BY 4.0, 728 MB, checked by sha256;
   about five minutes to load on four cores).
3. Prints the UI address, the owner's sign-in and the walkthrough (docs/demo.md).

Running it again changes nothing. With the stack, step 2 runs in the kernel image (`kk demo --load`).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .cli import ROOT

BPBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
SOSA = "http://www.w3.org/ns/sosa/"
O2C = "urn:kk:process:order-to-cash"
BPI = "urn:kk:process:bpi2019"
BPI_URL = "https://data.4tu.nl/file/35ed7122-966a-484e-a0e1-749b64e3366d/864493d1-3a58-47f6-ad6f-27f95f995828"
BPI_SHA256 = "af63bc687fc4152f2123b05c3af7772b37ef3fce2d3f67f812666c9e356baae7"   # pinned Oct 2026 (D-72)
CACHE = Path(os.environ.get("KK_EVAL_CACHE") or Path.home() / ".cache/knowledge-kernel/eval") / "bpi2019"
BPI_LOG = CACHE / "BPI_Challenge_2019.xes"
QUOTE = "Billing sends the invoice only after the goods\nhave shipped, and within two days of shipping."


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def fetch_bpi() -> Path:
    """The BPI Challenge 2019 log in the cache, downloaded once and checked by sha256."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if not BPI_LOG.exists() or sha256(BPI_LOG) != BPI_SHA256:
        part = BPI_LOG.with_suffix(".part")
        with urllib.request.urlopen(BPI_URL, timeout=600) as r, open(part, "wb") as f:
            while block := r.read(1 << 20):
                f.write(block)
        if sha256(part) != BPI_SHA256:
            sys.exit(f"{part}: sha256 does not match the pinned one")
        part.rename(BPI_LOG)
    return BPI_LOG


# ---------------------------------------------------------------- step 2, where the kernel is
def module(name: str, role: str, *args: str) -> dict:
    """Run a module command as its role (DATABASE_URL may SET ROLE to it); its last JSON line."""
    out = subprocess.run([sys.executable, "-m", name, *args], capture_output=True, text=True,
                         env={**os.environ, "KK_ROLE": role})
    if out.returncode:
        sys.exit(f"{name} failed: {(out.stderr or out.stdout)[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def procedure(agent: str) -> str:
    """The written procedure's ordering and time limit, extracted with their quote by the demo agent (the
    process-extraction skill, followed by hand); the procedure itself goes to the artifact store."""
    from .lib import connect, run, submit
    path = ROOT / "examples/order-to-cash/procedure.md"
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    store = Path(os.environ.get("KK_ARTIFACTS") or Path.home() / ".local/share/knowledge-kernel/artifacts")
    store.mkdir(parents=True, exist_ok=True)
    if not (store / sha).exists():
        (store / sha).write_bytes(data)
    ref = [{"artifact": sha, "quote": QUOTE}]
    step = lambda s: f"{O2C}:step:{s}"  # noqa: E731
    role = "agent_" + agent.replace("-", "_")
    with connect(role) as conn, conn.cursor() as cur:
        cs = {"idempotency_key": f"demo:procedure:{sha[:16]}", "attributed_to": f"urn:kk:agent:{agent}",
              "target_scope": "urn:kk:scope:default", "ops": [
                  {"op": "add_artifact", "sha256": sha, "media_type": "text/markdown", "byte_size": len(data),
                   "location": f"file://{store / sha}", "source_uri": "file:examples/order-to-cash/procedure.md",
                   "retrieved_at": datetime.now(timezone.utc).isoformat()},
                  {"op": "add_claim", "subject": step("ship-goods"), "predicate": BPBO + "has_nextFlowNode",
                   "object": {"entity": step("send-invoice")}, "evidence_type": "extracted", "references": ref},
                  {"op": "add_claim", "subject": step("send-invoice"), "predicate": BPBO + "maxValue",
                   "object": {"value": {"@value": 172800, "@type": "http://www.w3.org/2001/XMLSchema#integer"}},
                   "qualifiers": {SOSA + "observedProperty": "urn:kk:property:wait-seconds",
                                  BPBO + "has_previousFlowNode": step("ship-goods")},
                   "evidence_type": "extracted", "references": ref}]}
        with run(cur, None, {"skill": "process-extraction", "demo": True}) as (run_id, _):
            return str(submit(cur, cs, run_id)["id"])


def load(bpi: Path | None, agent: str) -> dict:
    if bpi:
        log = module("eventlog_encoder", "kkm_encoder_eventlog", "--process", BPI, "--window", "month", str(bpi))
        doc = module("bpmn_encoder", "kkm_encoder_bpmn", "--process", BPI, str(ROOT / "examples/bpi2019/documented.bpmn"))
        return {"process": BPI, "events": log.get("events"), "claims": log.get("claims"), "model": doc.get("change_set")}
    ex = ROOT / "examples/order-to-cash"
    log = module("eventlog_encoder", "kkm_encoder_eventlog", "--process", O2C, str(ex / "order-to-cash.csv"))
    doc = module("bpmn_encoder", "kkm_encoder_bpmn", "--process", O2C, str(ex / "order-to-cash.bpmn"))
    return {"process": O2C, "events": log.get("events"), "claims": log.get("claims"), "model": doc.get("change_set"),
            "procedure": procedure(agent)}


# ---------------------------------------------------------------- the command
def main(argv: list[str]) -> None:
    import argparse

    from . import up
    from .cli import name
    ap = argparse.ArgumentParser(prog="kk demo", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("--bpi", action="store_true", help="BPI Challenge 2019 (fetched) instead of order-to-cash")
    ap.add_argument("--project", type=Path, default=Path.home() / "kk-demo", help="the demo project folder")
    ap.add_argument("--agent", type=name, default="claude-code", help="the demo project's agent")
    ap.add_argument("--no-stack", action="store_true", help="use the kernel at KK_ADMIN_URL; start nothing")
    ap.add_argument("--db-host", help="Postgres host:port as the harness reaches it")
    ap.add_argument("--load", type=Path, nargs="?", const=Path(), help=argparse.SUPPRESS)  # step 2 only, here
    a = ap.parse_args(argv)
    if a.load is not None:     # inside the kernel image: DATABASE_URL may switch to each role
        print(json.dumps(load(a.load if a.bpi else None, a.agent)))
        return
    bpi = fetch_bpi() if a.bpi else None
    a.project.mkdir(parents=True, exist_ok=True)
    out = up.connect(a.project.resolve(), a.agent, a.no_stack, a.db_host, no_scan=True)
    if a.no_stack:
        os.environ["DATABASE_URL"] = out["admin_url"]
        loaded = load(bpi, a.agent)
    else:
        cmd = ["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "--env-file", str(ROOT / "deploy/.env"),
               "run", "--rm", "--no-deps", "-T", "-v", "kk-artifacts:/artifacts", "-e", "KK_ARTIFACTS=/artifacts",
               *(["-v", f"{bpi.parent}:/data:ro"] if bpi else []),
               "kernel", "kk", "demo", "--agent", a.agent, *(["--bpi", "--load", f"/data/{bpi.name}"] if bpi else ["--load"])]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            sys.exit(f"loading the demo failed: {(p.stderr or p.stdout)[-1500:]}")
        loaded = json.loads(p.stdout.strip().splitlines()[-1])
    shown = {k: v for k, v in out.items() if k != "admin_url"}
    print(json.dumps({**shown, "loaded": loaded}, indent=2))
    print(f"\nThe demo is loaded. Open {out.get('ui', 'the UI (kk serve, /ui/)')}, sign in as owner (owner_sign_in) and "
          f"follow docs/demo.md: the Processes tab first. The agent in {a.project} answers questions about the "
          "process with cited claims (Claude Code: `claude` there).", file=sys.stderr)

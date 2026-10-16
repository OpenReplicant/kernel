"""Event-log encoder: an event log (CSV or XES) into the kernel as observed claims. A plain command (D-41).

  python -m eventlog_encoder --process IRI [--window all|day|week|month] [--case COL] [--activity COL]
                             [--timestamp COL] [--resource COL] [--artifacts DIR] [--no-filter] LOG

1. Run the ingest filter on the log (fail closed; --no-filter skips it).
2. Store the log as an artifact and encode it into one change set (encode.py).
3. Supersede on re-ingest: claims this encoder made from an earlier version (or another windowing) of
   the same log for the same process are deprecated — superseded by the new claim with the same subject,
   predicate, qualifiers and window start, otherwise retracted. The same log again is a no-op (same
   idempotency key).

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_eventlog), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kk.lib import connect, ingest_filter, run, submit

from .encode import WINDOWS, encode, read_csv, read_xes, supersede

MODULE = "urn:kk:module:encoder-eventlog"
AGENT = "urn:kk:agent:encoder-eventlog"
SCOPE = "urn:kk:scope:default"
PRIOR = """select c.id, c.subject, c.predicate, c.qualifiers, c.valid_from, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and s.idempotency_key <> %s"""


def change_set(log: Path, data: bytes, a, artifacts: Path) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    if log.suffix.lower() == ".xes":
        events, media = read_xes(data), "application/xml"
    else:
        events, media = read_csv(data, a.case, a.activity, a.timestamp, a.resource or None), "text/csv"
    artifacts.mkdir(parents=True, exist_ok=True)
    stored = artifacts / sha
    if not stored.exists():
        stored.write_bytes(data)
    ops = [{"op": "add_artifact", "sha256": sha, "media_type": media, "byte_size": len(data),
            "location": f"file://{stored}", "source_uri": f"file:{log.name}",
            "retrieved_at": datetime.now(timezone.utc).isoformat()}]
    ops += encode(events, a.process, a.window, sha)
    prefix = f"eventlog:{a.process}|{log.name}|"
    return {"idempotency_key": f"{prefix}{a.window}|{sha[:16]}", "attributed_to": AGENT, "target_scope": SCOPE,
            "ops": ops, "_prefix": prefix, "_events": len(events)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Encode an event log (CSV or XES) into observed claims.")
    ap.add_argument("log")
    ap.add_argument("--process", required=True, help="the process IRI, e.g. urn:kk:process:order-to-cash")
    ap.add_argument("--window", choices=WINDOWS, default="all", help="observation window (default: the whole log)")
    ap.add_argument("--case", default="case_id")
    ap.add_argument("--activity", default="activity")
    ap.add_argument("--timestamp", default="timestamp")
    ap.add_argument("--resource", default="resource", help="resource column ('' for none)")
    ap.add_argument("--artifacts", default=os.environ.get("KK_ARTIFACTS")
                    or str(Path.home() / ".local/share/knowledge-kernel/artifacts"))
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    log = Path(a.log).resolve()
    if not a.no_filter:
        found = ingest_filter(log.parent, [log.name])
        if found["blocked"]:
            print(json.dumps({"blocked": found["blocked"]}))
            return 1
    cs = change_set(log, log.read_bytes(), a, Path(a.artifacts))
    prefix, n_events = cs.pop("_prefix"), cs.pop("_events")
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_eventlog")) as conn, conn.cursor() as cur:
        cur.execute(PRIOR, (AGENT, prefix, cs["idempotency_key"]))
        cols = [d.name for d in cur.description]
        prior = [dict(zip(cols, r)) for r in cur.fetchall()]
        if prior:
            supersede(cs, prior)
        with run(cur, MODULE, {"log": log.name, "process": a.process, "window": a.window}) as (run_id, outputs):
            res = submit(cur, cs, run_id)
            outputs.update(events=n_events, claims=sum(op["op"] == "add_claim" for op in cs["ops"]))
    print(json.dumps({"change_set": str(res["id"]), "status": res["status"], "events": n_events,
                      "claims": outputs["claims"], "superseded": len(prior)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

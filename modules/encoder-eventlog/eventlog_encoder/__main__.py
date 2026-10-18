"""Event-log encoder: an event log (CSV or XES) into the kernel as observed claims. A plain command (D-41).

  python -m eventlog_encoder --process IRI [--window all|day|week|month] [--case COL] [--activity COL]
                             [--timestamp COL] [--resource COL] [--artifacts DIR] [--batch N] [--no-filter] LOG

1. Run the ingest filter on the log (fail closed; --no-filter skips it).
2. Store the log as an artifact and encode it (encode.py) into change sets of at most --batch claims
   (default 5000), the first carrying the artifact. XES (also .xes.gz) is read as a stream, one trace at
   a time, and the file is hashed and copied in blocks, so memory does not grow with the log (M3-T4);
   a CSV is read whole (its rows need not be grouped by case).
3. Supersede on re-ingest: claims this encoder made from an earlier version (or another windowing) of
   the same log for the same process are deprecated — superseded by the new claim with the same subject,
   predicate, qualifiers and window start, otherwise retracted. The same log again is a no-op (same
   idempotency keys).

Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_encoder_eventlog), KK_INGEST_FILTER, KK_ARTIFACTS (default ~/.local/share/knowledge-kernel/artifacts).
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

from kk.lib import connect, ingest_filter, run, submit

from .batches import AGENT, change_sets, prefix, store, version
from .encode import WINDOWS

MODULE = "urn:kk:module:encoder-eventlog"
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
    ap.add_argument("--batch", type=int, default=5000, help="claims per change set (default 5000)")
    ap.add_argument("--no-filter", action="store_true", help="skip the ingest filter (secrets may be stored)")
    a = ap.parse_args(argv)
    log = Path(a.log).resolve()
    started = time.monotonic()
    if not a.no_filter:
        found = ingest_filter(log.parent, [log.name])
        if found["blocked"]:
            print(json.dumps({"blocked": found["blocked"]}))
            return 1
    filtered = time.monotonic()
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_eventlog")) as conn, conn.cursor() as cur:
        with run(cur, MODULE, {"log": log.name, "process": a.process, "window": a.window}) as (run_id, outputs):
            sha, size = store(log, Path(a.artifacts))
            cur.execute(PRIOR, (AGENT, prefix(log, a), version(log, a, sha)))
            cols = [d.name for d in cur.description]
            prior = [dict(zip(cols, r)) for r in cur.fetchall()]
            sets, n_events = change_sets(log, a, Path(a.artifacts), sha, size, prior)
            encoded = time.monotonic()
            ids = [str(submit(cur, cs, run_id)["id"]) for cs in sets]
            n_claims = sum(op["op"] == "add_claim" for cs in sets for op in cs["ops"])
            outputs.update(events=n_events, claims=n_claims, change_sets=len(ids))
    done = time.monotonic()
    print(json.dumps({"change_set": ids[-1], "change_sets": ids, "events": n_events, "claims": n_claims,
                      "superseded": len(prior), "seconds": {"filter": round(filtered - started, 1), "encode": round(encoded - filtered, 1),
                                  "submit": round(done - encoded, 1)},
                      "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}))
    return 0


# This encoder's current claims from earlier versions of a log: change sets under its prefix whose key
# is not one of this version's.
PRIOR = """select c.id, c.subject, c.predicate, c.qualifiers, c.valid_from, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and not starts_with(s.idempotency_key, %s)"""


if __name__ == "__main__":
    sys.exit(main())

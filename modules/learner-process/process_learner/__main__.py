"""Process learner worker (D-76): proposes adding to the documented process the paths that people
confirmed as deviations and that practice takes in a large share of cases. Needs no runner.

It listens on kernel_change_set and runs a pass whenever a change set is applied, plus a sweep every
KK_SWEEP_SECONDS. A pass reads the process claims as conformance does (conformance.compare), the
discrepancies a person confirmed (current claims of the conformance processor), and submits one proposal
per learnable discrepancy not proposed before (learn.py), in the review lane.

  python -m process_learner [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_learner_process), KK_LEARN_SHARE (default 0.05), KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

from conformance.__main__ import AGENT as CONFORMANCE, CLAIMS
from conformance.compare import CONTAINER, LABEL, MAX, NEXT, RESULT, discrepancies, value
from kk.lib import connect, listen, run, submit

from .learn import proposals

MODULE = "urn:kk:module:learner-process"
SHARE = float(os.environ.get("KK_LEARN_SHARE", "0.05"))
CONFIRMED = """select subject, array_agg(id::text) from api.claims
               where attributed_to = %s and subject like 'urn:kk:discrepancy:%%' and status <> 'deprecated'
               group by subject"""


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(CLAIMS, {"p": [NEXT, CONTAINER, RESULT, MAX], "label": LABEL, "c": CONTAINER})
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        labels = {r["subject"]: str(value(r)) for r in rows if r["predicate"] == LABEL and value(r) is not None}
        confirmed = defaultdict(list, cur.execute(CONFIRMED, (CONFORMANCE,)).fetchall())
        todo = proposals(discrepancies(rows), confirmed, labels, SHARE)
        made = {k for (k,) in cur.execute("select idempotency_key from api.change_sets where idempotency_key = any(%s)",
                                          ([c["idempotency_key"] for c in todo],)).fetchall()}
        todo = [c for c in todo if c["idempotency_key"] not in made]
        if not todo:
            return {"proposed": {}}
        with run(cur, MODULE, {"proposals": len(todo), "share": SHARE}) as (run_id, outputs):
            proposed = {c["idempotency_key"]: str(submit(cur, c, run_id)["id"]) for c in todo}
            outputs.update(proposed=len(proposed))
    return {"proposed": proposed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Propose revisions of documented processes from confirmed practice.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_learner_process"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, wake=lambda payload: json.loads(payload).get("status") == "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

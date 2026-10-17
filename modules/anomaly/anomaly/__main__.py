"""Anomaly worker (the Analyse step of MAPE-K, D-53): after every applied change set (and every
KK_SWEEP_SECONDS) it reads the services' current observations and documented limits and submits one
change set per new anomaly (detect.py). Anomalies are analysis, not decisions: they are applied
directly; the plan an agent derives from them goes to review.

  python -m anomaly [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_anomaly), KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from kk.lib import connect, listen, run, submit

from .detect import MAX, RESULT, anomalies, change_set

MODULE = "urn:kk:module:anomaly"
AGENT = "urn:kk:agent:anomaly"
SCOPE = "urn:kk:scope:default"
COLUMNS = "id, subject, qualifiers, object_value, valid_from, valid_to"


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"""select {COLUMNS} from api.claims where predicate = %s and subject like 'urn:kk:node:%%'
                        and evidence_type = 'observed' and status <> 'deprecated'
                        order by valid_from desc limit 5000""", (RESULT,))
        cols = [d.name for d in cur.description]
        obs = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(f"""select {COLUMNS} from api.claims where predicate = %s and subject like 'urn:kk:node:%%'
                        and status <> 'deprecated'""", (MAX,))
        limits = [dict(zip(cols, r)) for r in cur.fetchall()]
        found = anomalies(obs, limits)
        if not found:
            return {"anomalies": {}}
        cur.execute("select idempotency_key from api.change_sets where idempotency_key = any(%s)",
                    ([f"anomaly:{a['iri']}" for a in found],))
        done = {r[0] for r in cur.fetchall()}
        new = [a for a in found if f"anomaly:{a['iri']}" not in done]
        submitted = {}
        if new:
            with run(cur, MODULE, {"anomalies": len(new)}) as (run_id, outputs):
                for a in new:
                    submitted[a["iri"]] = str(submit(cur, change_set(a, AGENT, SCOPE), run_id)["id"])
                outputs.update(anomalies=len(new))
    return {"anomalies": submitted}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Claim anomalies in the services' observations.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_anomaly"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, wake=lambda payload: json.loads(payload).get("status") == "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Process KPIs: per process and time window, from the event logs the encoder stored, as observed claims (D-119).
Needs no runner; also `kk serve` part `kpis` (opt in).

For each process with event-log observations (case counts that cite a log artifact) it reads the log from the
artifact store, computes the KPIs (kpis.py) and submits one change set of observed claims: SOSA observations
of the process (subject the process, valid time the window, sosa:observedProperty urn:kk:property:*), each
referencing the log artifact, in the scope of the encoder's claims. A person's targets (bbo:maxValue or
bbo:minValue on the process, qualifier sosa:observedProperty the KPI, a claim by a person in the same scope) mark
the observation `met` or `missed` in a qualifier. The idempotency key carries a hash of the logs, window setting
and targets, so nothing changed is a no-op; a recompute supersedes the earlier values (the mark is not part of
a claim's identity), and windows that no longer exist are retracted.

  python -m process_kpis [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default kkm_process_kpis),
KK_KPI_WINDOW (all, day, week, month; default day), KK_ARTIFACTS (the artifact store), KK_LOG_COLUMNS
(CSV columns as case,activity,timestamp,resource,cost; default case_id,activity,timestamp,resource,cost),
KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from kk.changes import prior as prior_claims, supersede
from kk.lib import connect, listen, manifest_patterns, run, submit

from .kpis import KPIS, MAX, MIN, OBSERVED_PROPERTY, PROPERTY, RESULT, compute, ops, read_cases

MODULE = "urn:kk:module:process-kpis"
AGENT = "urn:kk:agent:process-kpis"
WINDOW = os.environ.get("KK_KPI_WINDOW", "day")
STORE = Path(os.environ.get("KK_ARTIFACTS") or Path.home() / ".local/share/knowledge-kernel/artifacts")
COLUMNS = dict(zip(("case", "activity", "timestamp", "resource", "cost"),
                   (os.environ.get("KK_LOG_COLUMNS") or "").split(","))) if os.environ.get("KK_LOG_COLUMNS") else {}
# The processes with logs: current case counts of a process (not a step), the artifacts they cite and their scope.
LOGS = """select c.subject, c.scope, r.artifact from api.claims c join api.claim_references r on r.claim_id = c.id
          where c.status <> 'deprecated' and c.predicate = %s and c.evidence_type = 'observed'
            and c.qualifiers->>%s = %s and r.artifact is not null and position(':step:' in c.subject) = 0
          group by 1, 2, 3"""
TARGETS = """select id, subject, predicate, object_value->>'@value' as value, qualifiers->>%s as kpi, scope
             from api.claims where status <> 'deprecated' and predicate = any(%s) and attributed_to like 'urn:kk:agent:person:%%'"""


# Qualifier keys that mark an observation against a target; not part of a claim's identity.
MARKS = (PROPERTY + "target-status", MAX, MIN)


def read(sha: str):
    path = STORE / sha
    if not path.exists():
        return None
    data = path.read_bytes()
    return data if hashlib.sha256(data).hexdigest() == sha else None


def one_pass(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(LOGS, (RESULT, OBSERVED_PROPERTY, PROPERTY + "case-count"))
        logs = defaultdict(list)
        for process, scope, sha in cur.fetchall():
            logs[(process, scope)].append(sha)
        cur.execute(TARGETS, (OBSERVED_PROPERTY, [MAX, MIN]))
        targets = defaultdict(list)       # (process, scope) -> kpi -> [target]
        for id_, subject, predicate, value, kpi, scope in cur.fetchall():
            if kpi and kpi.startswith(PROPERTY) and kpi[len(PROPERTY):] in KPIS:
                try:
                    targets[(subject, scope)].append({"id": str(id_), "kpi": kpi[len(PROPERTY):], "kind": predicate,
                                                     "value": float(value) if "." in str(value) else int(value)})
                except (TypeError, ValueError):
                    pass
        done = {}
        with run(cur, MODULE, {"window": WINDOW}) as (run_id, outputs):
            for (process, scope), shas in sorted(logs.items()):
                cases = []
                for sha in sorted(shas):
                    data = read(sha)
                    if data is None:
                        print(f"kpis: {process}: log {sha[:12]} is not in this artifact store", flush=True)
                        break
                    cases += read_cases(data, COLUMNS)
                else:
                    mine = targets.get((process, scope), [])
                    by_kpi = {k: [t for t in mine if t["kpi"] == k] for k in KPIS}
                    refs = [{"artifact": sha} for sha in sorted(shas)]
                    window = compute(cases, WINDOW)
                    digest = hashlib.sha256(json.dumps([sorted(shas), WINDOW, sorted((t["id"], t["value"]) for t in mine)]).encode()).hexdigest()[:16]
                    prefix = f"kpis:{process}|{scope}|"
                    cs = {"idempotency_key": prefix + digest, "attributed_to": AGENT, "target_scope": scope,
                          "ops": ops(process, window, by_kpi, refs)}
                    if not cs["ops"]:
                        continue
                    old = prior_claims(cur, AGENT, prefix, cs["idempotency_key"])
                    if old:
                        supersede(cs, old, ignore=MARKS)
                    done[process] = str(submit(cur, cs, run_id)["id"])
            outputs.update(processes=len(done))
    return {"processes": done}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute process KPIs from event logs; submit them as observed claims.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    conn = connect(os.environ.get("KK_ROLE", "kkm_process_kpis"))
    if a.once:
        print(json.dumps(one_pass(conn)))
        return 0
    listen(conn, one_pass, patterns=manifest_patterns("process-kpis"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""BPM on the kernel (milestone 2), run by scripts/smoke_kernel.sh after the kernel checks.

M2-T1: the event-log encoder (a plain command, ingest filter first) turns the sample order-to-cash log
into observed claims: the expected steps and directly-follows counts; the same log again is a no-op;
a changed log supersedes the observations it changes.

Env: DATABASE_URL (kk_authenticator), OWNER_DATABASE_URL (reads); gitleaks on PATH; the
knowledge-kernel package installed (pip install -e .).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "examples/order-to-cash/order-to-cash.csv"
P = "urn:kk:process:order-to-cash"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
SOSA = "http://www.w3.org/ns/sosa/"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def encode(log: Path, artifacts: str) -> dict:
    out = subprocess.run([sys.executable, "-m", "eventlog_encoder", "--process", P, "--artifacts", artifacts, str(log)],
                         capture_output=True, text=True, env={**os.environ, "KK_ROLE": "kkm_encoder_eventlog"})
    if out.returncode != 0:
        raise SystemExit(f"encoder failed: {out.stderr[-1500:]}")
    return json.loads(out.stdout)


def settled(db, cs: str) -> str:
    for _ in range(240):
        status = db.execute("select status from api.change_sets where id = %s", (cs,)).fetchone()[0]
        if status != "submitted":
            return status
        time.sleep(0.25)
    return "submitted"


def observed(db, subject: str, prop: str, prev: str | None = None, status: str = "current"):
    q = {SOSA + "observedProperty": "urn:kk:property:" + prop, **({BBO + "has_previousFlowNode": prev} if prev else {})}
    rows = db.execute("""select (object_value->>'@value')::int, status from api.claims
                         where subject = %s and predicate = %s and qualifiers = %s::jsonb""",
                      (subject, SOSA + "hasSimpleResult", json.dumps(q))).fetchall()
    return [v for v, s in rows if (s != "deprecated") == (status == "current")]


def main() -> int:
    db = psycopg.connect(os.environ["OWNER_DATABASE_URL"], autocommit=True)
    step = lambda name: f"{P}:step:{name}"
    with tempfile.TemporaryDirectory() as tmp:
        art = str(Path(tmp) / "artifacts")
        first = encode(LOG, art)
        check("M2-T1 the sample log is encoded and applied", settled(db, first["change_set"]) == "applied",
              f"({first['events']} events, {first['claims']} claims)")
        labels = db.execute("""select count(distinct subject) from api.claims
                               where subject like %s and predicate = 'http://www.w3.org/2000/01/rdf-schema#label'""",
                            (P + ":step:%",)).fetchone()[0]
        edges = db.execute("""select count(*) from api.claims where predicate = %s and evidence_type = 'observed'
                              and subject like %s""", (BBO + "has_nextFlowNode", P + ":step:%")).fetchone()[0]
        check("M2-T1 six steps and ten observed directly-follows edges", labels == 6 and edges == 10,
              f"({labels} steps, {edges} edges)")
        check("M2-T1 counts: 40 cases; check credit → ship goods 28; check credit reworked twice",
              observed(db, P, "case-count") == [40]
              and observed(db, step("ship-goods"), "directly-follows-count", step("check-credit")) == [28]
              and observed(db, step("check-credit"), "directly-follows-count", step("check-credit")) == [2])
        refs = db.execute("""select count(*) from api.claims c where c.change_set = %s
                             and not exists (select 1 from kernel.claim_reference r where r.claim_id = c.id)""",
                          (first["change_set"],)).fetchone()[0]
        check("M2-T1 every claim references the log artifact", refs == 0)
        again = encode(LOG, art)
        check("M2-T1 the same log again is a no-op", again["change_set"] == first["change_set"])

        changed = Path(tmp) / LOG.name                       # same name, last case dropped
        lines = LOG.read_text().splitlines(keepends=True)
        changed.write_text("".join(l for l in lines if not l.startswith("O-1040,")))
        second = encode(changed, art)
        status = settled(db, second["change_set"])
        reasons = dict(db.execute("""select s.reason, count(*) from kernel.claim_status s
                                     join kernel.change_set cs on cs.id = s.change_set
                                     where cs.id = %s group by 1""", (second["change_set"],)).fetchall())
        check("M2-T1 a changed log supersedes the observations it changes",
              status == "applied" and observed(db, P, "case-count") == [39]
              and observed(db, P, "case-count", status="deprecated") == [40] and reasons.get("superseded", 0) > 0,
              f"({second['superseded']} earlier claims deprecated: {reasons})")
    print(f"M2 BPM passed ({sum(checks)}/{len(checks)})" if all(checks) else "M2 BPM FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

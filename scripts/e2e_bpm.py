"""BPM on the kernel (milestone 2), run by scripts/smoke_kernel.sh after the kernel checks.

M2-T1: the event-log encoder (a plain command, ingest filter first) turns the sample order-to-cash log
into observed claims: the expected steps and directly-follows counts; the same log again is a no-op;
a changed log supersedes the observations it changes.
M2-T2: the BPMN encoder gives the documented process on the same step IRIs, so documented and observed
flows between two tasks are the same statement; an agent following the process-extraction skill records
the written procedure as extracted claims, and the kernel refuses an extracted claim without its quote.

Env: DATABASE_URL (kk_authenticator), OWNER_DATABASE_URL (reads), AGENT_DATABASE_URL (the agent); gitleaks on PATH; the
knowledge-kernel package installed (pip install -e .).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from kk.lib import run, submit as lib_submit

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples/order-to-cash"
LOG = EX / "order-to-cash.csv"
P = "urn:kk:process:order-to-cash"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
SOSA = "http://www.w3.org/ns/sosa/"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def encode(log: Path, artifacts: str, module: str = "eventlog_encoder", role: str = "kkm_encoder_eventlog") -> dict:
    out = subprocess.run([sys.executable, "-m", module, "--process", P, "--artifacts", artifacts, str(log)],
                         capture_output=True, text=True, env={**os.environ, "KK_ROLE": role})
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


def documented(db, step, art: str) -> None:
    res = encode(EX / "order-to-cash.bpmn", art, "bpmn_encoder", "kkm_encoder_bpmn")
    check("M2-T2 the sample BPMN is encoded and applied", settled(db, res["change_set"]) == "applied",
          f"({res['claims']} claims)")
    kinds = dict(db.execute("""select object_entity, count(*) from api.claims where change_set = %s
                               and predicate = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type' group by 1""",
                            (res["change_set"],)).fetchall())
    tasks = sum(n for k, n in kinds.items() if k.endswith("Task"))
    check("M2-T2 the documented process: 6 tasks, a gateway, 3 events, 4 roles",
          tasks == 6 and kinds.get(BBO + "ExclusiveGateway") == 1 and kinds.get(BBO + "Role") == 4
          and kinds.get(BBO + "StartEvent", 0) + kinds.get(BBO + "EndEvent", 0) == 3,
          str({k[len(BBO):]: n for k, n in kinds.items()}))
    both = db.execute("""select count(distinct evidence_type) from api.claims where subject = %s and predicate = %s
                         and object_entity = %s and status <> 'deprecated'""",
                      (step("ship-goods"), BBO + "has_nextFlowNode", step("send-invoice"))).fetchone()[0]
    check("M2-T2 documented and observed ship → invoice meet on one statement (deterministic + observed)", both == 2)


def extracted() -> None:
    """The process-extraction skill, followed by hand: the agent records one ordering and the time
    limit from the written procedure, under its own login."""
    text = (EX / "procedure.md").read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    quote = "Billing sends the invoice only after the goods\nhave shipped, and within two days of shipping."
    assert quote in text
    ref = [{"source_uri": "file:examples/order-to-cash/procedure.md", "quote": quote}]
    ops = [{"op": "add_claim", "subject": f"{P}:step:ship-goods", "predicate": BBO + "has_nextFlowNode",
            "object": {"entity": f"{P}:step:send-invoice"}, "evidence_type": "extracted", "references": ref},
           {"op": "add_claim", "subject": f"{P}:step:send-invoice", "predicate": BBO + "maxValue",
            "object": {"value": {"@value": 172800, "@type": "http://www.w3.org/2001/XMLSchema#integer"}},
            "qualifiers": {SOSA + "observedProperty": "urn:kk:property:wait-seconds",
                           BBO + "has_previousFlowNode": f"{P}:step:ship-goods"},
            "evidence_type": "extracted", "references": ref}]
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    me = agent.execute("select iri from kernel.agent where db_role = current_user").fetchone()[0]

    def submit(key, ops):
        cs = {"idempotency_key": key, "attributed_to": me, "target_scope": "urn:kk:scope:default", "ops": ops}
        try:
            with agent.cursor() as cur, run(cur, None, {"skill": "process-extraction"}) as (run_id, _):
                return settled(agent, lib_submit(cur, cs, run_id)["id"])
        except psycopg.Error as e:
            return f"refused: {str(e).splitlines()[0]}"

    unquoted = [{**op, "references": [{"source_uri": ref[0]["source_uri"]}]} for op in ops[:1]]
    status = submit(f"process-text:{P}|procedure.md|{sha}:noquote", unquoted)
    check("M2-T2 an extracted claim without its quote is refused", status != "applied", f"({status[:90]})")
    status = submit(f"process-text:{P}|procedure.md|{sha}", ops)
    check("M2-T2 the procedure's ordering and time limit are recorded as extracted claims", status == "applied",
          f"({status})")


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
        documented(db, step, art)
        extracted()
    print(f"M2 BPM passed ({sum(checks)}/{len(checks)})" if all(checks) else "M2 BPM FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

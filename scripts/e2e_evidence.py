"""D-62: belief from evidence, run by scripts/smoke_kernel.sh while `kk serve` runs the belief model.

A proposition's label claim carries the belief that it holds: one work supporting it raises it above
0.5; two independent works refuting it, recorded later, bring it below; a second extraction of the same
work changes nothing.

Env: AGENT_DATABASE_URL; the knowledge-kernel package installed.
"""
import hashlib
import os
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def submit(db, key: str, ops: list) -> None:
    me = db.execute("select kernel.my_agent()").fetchone()[0]
    run = db.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "evidence smoke"}),)).fetchone()[0]
    db.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"evidence-smoke:{key}", "attributed_to": me, "generated_by": str(run),
        "target_scope": "urn:kk:scope:default", "ops": ops}),))


def stance(work: str, predicate: str, prop: str, quote: str) -> dict:
    return {"op": "add_claim", "subject": work, "predicate": predicate, "object": {"entity": prop},
            "evidence_type": "extracted", "references": [{"source_uri": work, "quote": quote}]}


def belief(db, prop: str, want, wait: float = 60):
    """The label claim's belief once it equals `want` (or after `wait` seconds)."""
    deadline = time.time() + wait
    while True:
        got = db.execute("select belief from api.claims where subject = %s and predicate = %s",
                         (prop, "http://www.w3.org/2000/01/rdf-schema#label")).fetchone()
        got = got and got[0]
        if (got is not None and abs(got - want) < 1e-3) or time.time() > deadline:
            return got
        time.sleep(1)


def main() -> int:
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    stamp = str(int(time.time() * 1000))
    text = f"Compound K{stamp} lowers blood pressure in adults"
    prop = "urn:kk:proposition:" + hashlib.sha256(text.encode()).hexdigest()[:32]
    works = [f"urn:kk:paper:smoke:{stamp}-{i}" for i in range(3)]
    submit(agent, f"{stamp}:a", [
        {"op": "add_claim", "subject": prop, "predicate": "rdfs:label", "object": {"value": {"@value": text, "@language": "en"}},
         "evidence_type": "testimony", "references": [{"source_uri": "file:scripts/e2e_evidence.py"}]},
        stance(works[0], "http://purl.org/spar/cito/supports", prop, "K lowered systolic pressure by 9 mmHg.")])
    got = belief(agent, prop, 0.6667)
    check("D-62 one work supporting a proposition raises its belief above 0.5", got is not None and abs(got - 0.6667) < 1e-3, got)
    submit(agent, f"{stamp}:b", [stance(works[0], "http://purl.org/spar/cito/supports", prop, "K lowered systolic pressure by 9 mmHg in the trial.")])
    time.sleep(3)
    got = belief(agent, prop, 0.6667, wait=10)
    check("D-62 a second extraction from the same work changes nothing", got is not None and abs(got - 0.6667) < 1e-3, got)
    submit(agent, f"{stamp}:c", [stance(works[1], "http://purl.org/spar/cito/refutes", prop, "K had no effect on blood pressure."),
                                 stance(works[2], "http://purl.org/spar/cito/refutes", prop, "No change in pressure was seen with K.")])
    got = belief(agent, prop, 0.4)
    check("D-62 two independent works refuting it, recorded later, bring it below 0.5", got is not None and abs(got - 0.4) < 1e-3, got)
    why = agent.execute("""select api.explain(id)->'beliefs'->-1->'explanation' from api.claims
                           where subject = %s and predicate = %s""",
                        (prop, "http://www.w3.org/2000/01/rdf-schema#label")).fetchone()
    why = (why and why[0]) or {}
    check("D-62 the belief explains itself: the works for and against",
          why.get("works_for") == 1 and why.get("works_against") == 2, why)
    print(f"D-62 belief from evidence passed ({sum(checks)}/{len(checks)})" if all(checks) else "D-62 belief from evidence FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

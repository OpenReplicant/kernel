"""M3-T6: the quote verifier, run by scripts/smoke_kernel.sh while `kk serve` runs the `quotes` part.

An agent records three extracted claims: one quoting the order-to-cash procedure verbatim (a file:
source), one with a quote the procedure does not contain, and one quoting an artifact it stored. The
verifier supports the two real quotes and refutes the fabricated one; belief caps the refuted claim
(D-67); the agent cannot verify its own claim (invariant 5).

Env: AGENT_DATABASE_URL, KK_ARTIFACTS (the store `kk serve` reads); the knowledge-kernel package installed.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

METHOD = "urn:kk:module:verifier-quotes"
PROCEDURE = "file:examples/order-to-cash/procedure.md"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def submit(db, key: str, ops: list) -> dict:
    me = db.execute("select kernel.my_agent()").fetchone()[0]
    run = db.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "quote smoke"}),)).fetchone()[0]
    return db.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"quote-smoke:{key}", "attributed_to": me, "generated_by": str(run),
        "target_scope": "urn:kk:scope:default", "ops": ops}),)).fetchone()[0]


def extracted(subject: str, text: str, ref: dict) -> dict:
    return {"op": "add_claim", "subject": subject, "predicate": "rdfs:label",
            "object": {"value": {"@value": text, "@language": "en"}}, "evidence_type": "extracted",
            "references": [ref]}


def wait(db, sql: str, args, ok, timeout: float = 60):
    deadline = time.time() + timeout
    while True:
        got = db.execute(sql, args).fetchone()
        if ok(got) or time.time() > deadline:
            return got
        time.sleep(1)


def main() -> int:
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    stamp = str(int(time.time() * 1000))
    note = f"Interview note {stamp}: refunds above 500 EUR need a second approver.".encode()
    sha = hashlib.sha256(note).hexdigest()
    store = Path(os.environ["KK_ARTIFACTS"])
    (store / sha).write_bytes(note)
    real, fake, art = (f"urn:kk:test:quotes:{stamp}:{k}" for k in ("real", "fake", "artifact"))
    submit(agent, stamp, [
        {"op": "add_artifact", "sha256": sha, "media_type": "text/plain", "byte_size": len(note),
         "location": f"file://{store}/{sha}"},
        extracted(real, "Billing invoices after shipping",
                  {"source_uri": PROCEDURE, "quote": "Billing sends the invoice only after the goods\nhave shipped"}),
        extracted(fake, "Billing invoices before shipping",
                  {"source_uri": PROCEDURE, "quote": "Billing sends the invoice before the goods ship"}),
        extracted(art, "Refunds above 500 EUR need a second approver",
                  {"artifact": sha, "quote": "refunds above 500 EUR need a second approver"})])

    q = """select v.verdict, v.details from api.verifications v join api.claims c on c.id = v.claim_id
           where c.subject = %s and v.method = %s"""
    got = {s: wait(agent, q, (s, METHOD), lambda r: r is not None) for s in (real, fake, art)}
    check("M3-T6 a verbatim quote of a file: source is supported (across a line break)",
          got[real] and got[real][0] == "supported", got[real] and got[real][1].get("references"))
    check("M3-T6 a fabricated quote is refuted", got[fake] and got[fake][0] == "refuted")
    check("M3-T6 a verbatim quote of an artifact is supported", got[art] and got[art][0] == "supported")
    b = "select belief from api.claims where subject = %s"
    fake_belief = wait(agent, b, (fake,), lambda r: r and r[0] is not None and r[0] <= 0.1)[0]
    real_belief = wait(agent, b, (real,), lambda r: r and r[0] is not None)[0]
    check("D-67 belief caps the refuted claim; the supported one keeps its corroboration",
          fake_belief is not None and fake_belief <= 0.1 and real_belief is not None and real_belief > 0.1,
          f"(refuted {fake_belief}, supported {real_belief})")
    claim = agent.execute("select id from api.claims where subject = %s", (fake,)).fetchone()[0]
    try:
        res = submit(agent, f"{stamp}:self", [{"op": "add_verification", "claim": str(claim), "verdict": "supported",
                                                "method": "urn:kk:agent:claude-code"}])
        refused = res["status"] != "applied"
    except psycopg.Error:
        refused = True
    check("M3-T6 the agent cannot verify its own claim (invariant 5)", refused)
    print(f"M3-T6 quote verifier passed ({sum(checks)}/{len(checks)})" if all(checks) else "M3-T6 quote verifier FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

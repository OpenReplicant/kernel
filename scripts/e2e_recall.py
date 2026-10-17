"""D-60: the recall index (BM25), run by scripts/smoke_kernel.sh while `kk serve` runs every part.

Search ranks with the index once it has rows; a new claim is found within a pass; claims of a scope the
caller may not read are not found and their term statistics are not visible (the index carries the
kernel's row-level security); a redacted claim leaves the index.

Env: KK_ADMIN_URL (superuser), OWNER_DATABASE_URL (a steward), AGENT_DATABASE_URL; the
knowledge-kernel package installed.
"""
import json
import os
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

from kernel_mcp import recall

PRIVATE = "urn:kk:scope:e2e-recall-private"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def submit(db, me: str, scope: str, subject: str, text: str) -> None:
    run = db.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "recall smoke"}),)).fetchone()[0]
    db.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"recall-smoke:{subject}", "attributed_to": me, "generated_by": str(run),
        "target_scope": scope, "ops": [{"op": "add_claim", "subject": subject, "predicate": "rdfs:label",
                                         "object": {"value": {"@value": text, "@language": "en"}},
                                         "evidence_type": "testimony",
                                         "references": [{"source_uri": "file:scripts/e2e_recall.py"}]}]}),))


def found(db, text: str, subject: str, wait: float = 30) -> bool:
    deadline = time.time() + wait
    while True:
        rows = db.execute(recall.VIA_INDEX, recall.params(text, 20)).fetchone()[0]
        if any(r["subject"] == subject for r in rows) or time.time() > deadline:
            return any(r["subject"] == subject for r in rows)
        time.sleep(1)


def main() -> int:
    admin = psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True)
    owner = psycopg.connect(os.environ["OWNER_DATABASE_URL"], autocommit=True)
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    me = lambda db: db.execute("select kernel.my_agent()").fetchone()[0]  # noqa: E731

    for _ in range(60):
        if recall.indexed(agent):
            break
        time.sleep(1)
    check("D-60 search ranks with the recall index (BM25) once it has rows", recall.indexed(agent)
          and recall.ranked(True) == recall.VIA_INDEX)

    stamp = int(time.time() * 1000)
    mine = f"urn:kk:test:recall:{stamp}"
    submit(agent, me(agent), "urn:kk:scope:default", mine, "Quixotic xylophone calibration protocol")
    check("D-60 a new claim is found by BM25 within a pass", found(agent, "xylophone calibration", mine))
    hit = agent.execute(recall.VIA_INDEX, recall.params("xylophone calibration", 5)).fetchone()[0][0]
    check("D-60 hits carry id, belief and a BM25 rank", hit["subject"] == mine and float(hit["rank"]) > 0 and "belief" in hit,
          f"rank {hit['rank']}")

    # A scope only the owner may read, and the services that must: the validator (to apply its change
    # sets), the belief model and the index.
    admin.execute("insert into kernel.scope(iri, label) values (%s, 'recall smoke: private') on conflict do nothing", (PRIVATE,))
    for role, write in (("person_owner", True), ("kkm_validator", False), ("kkm_belief", False),
                        ("kkm_recall_index", False)):
        admin.execute("""insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)
                         on conflict (scope, db_role) do update set can_read = true, can_write = excluded.can_write""",
                      (PRIVATE, role, write))
    secret = f"urn:kk:test:recall:private:{stamp}"
    submit(owner, me(owner), PRIVATE, secret, "Zephyrine marmalade ledger")
    check("D-60 the owner finds a claim of a scope they may read", found(owner, "zephyrine marmalade", secret))
    check("D-60 an agent without that clearance does not", not found(agent, "zephyrine marmalade", secret, wait=0))
    terms = agent.execute("select count(*) from recall.term where scope = %s", (PRIVATE,)).fetchone()[0]
    docs = agent.execute("select count(*) from recall.doc where scope = %s", (PRIVATE,)).fetchone()[0]
    check("D-60 nor its index rows or term statistics", terms == 0 and docs == 0, f"{terms} terms, {docs} docs")

    claim = agent.execute("select id from api.claims where subject = %s", (mine,)).fetchone()[0]
    run = owner.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "redact"}),)).fetchone()[0]
    owner.execute("select api.redact('claim', %s, 'recall smoke', %s)", (str(claim), run))
    deadline = time.time() + 30
    while time.time() < deadline and agent.execute("select count(*) from recall.doc where claim_id = %s", (claim,)).fetchone()[0]:
        time.sleep(1)
    check("D-60 a redacted claim leaves the index and is no longer found",
          not agent.execute("select count(*) from recall.doc where claim_id = %s", (claim,)).fetchone()[0]
          and not found(agent, "xylophone calibration", mine, wait=0))
    print(json.dumps({"index": dict(agent.execute("select scope, n from recall.corpus").fetchall())}))
    print(f"D-60 recall index passed ({sum(checks)}/{len(checks)})" if all(checks) else "D-60 recall index FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

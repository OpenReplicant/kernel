"""D-61: dense recall fused with BM25, run by scripts/smoke_kernel.sh while `kk serve` runs the `dense` part
(with KK_RERANK=1, so the worker downloads the cross-encoder; D-66).

Search fuses BM25 with embeddings once the dense index has rows; a paraphrase that shares no word with a
claim finds it (BM25 alone does not); a scope the caller may not read keeps its embeddings from them;
a redacted claim leaves the index. D-66: recall re-ranks the fused pool with the cross-encoder, which puts
the claim that answers a question above one that only shares its words; KK_RERANK=0 keeps the fused order.

Env: KK_ADMIN_URL (superuser), OWNER_DATABASE_URL (a steward), AGENT_DATABASE_URL; the
knowledge-kernel package installed with the `dense` extra; pgvector in Postgres.
"""
import json
import os
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

from kernel_mcp import recall

PRIVATE = "urn:kk:scope:e2e-dense-private"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def submit(db, scope: str, subject: str, text: str) -> None:
    me = db.execute("select kernel.my_agent()").fetchone()[0]
    run = db.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "dense smoke"}),)).fetchone()[0]
    db.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"dense-smoke:{subject}", "attributed_to": me, "generated_by": str(run),
        "target_scope": scope, "ops": [{"op": "add_claim", "subject": subject, "predicate": "rdfs:label",
                                         "object": {"value": {"@value": text, "@language": "en"}},
                                         "evidence_type": "testimony",
                                         "references": [{"source_uri": "file:scripts/e2e_dense.py"}]}]}),))


def embedded(db, subject: str, wait: float = 60) -> bool:
    deadline = time.time() + wait
    q = "select count(*) from recall_dense.vec v join api.claims c on c.id = v.claim_id where c.subject = %s"
    while not db.execute(q, (subject,)).fetchone()[0] and time.time() < deadline:
        time.sleep(1)
    return bool(db.execute(q, (subject,)).fetchone()[0])


def hits(db, text: str) -> list[str]:
    return [r["subject"] for r in db.execute(*recall.plan(db, text, 10)).fetchone()[0]]


def main() -> int:
    admin = psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True)
    owner = psycopg.connect(os.environ["OWNER_DATABASE_URL"], autocommit=True)
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)

    stamp = int(time.time() * 1000)
    mine = f"urn:kk:test:dense:{stamp}"
    submit(agent, "urn:kk:scope:default", mine, "A cat is sleeping on the doormat")
    check("D-61 a new claim is embedded within a pass", embedded(agent, mine))
    plan = recall.plan(agent, "feline napping on a rug", 10)
    check("D-61 search fuses BM25 with dense recall once the dense index has rows",
          plan[0] == recall.VIA_HYBRID and plan[1]["vec"], recall.dense(agent))
    bm25 = [r["subject"] for r in agent.execute(recall.VIA_INDEX, recall.params("feline napping on a rug", 10)).fetchone()[0]]
    check("D-61 a paraphrase sharing no word is found by the fused recall, not by BM25 alone",
          mine in hits(agent, "feline napping on a rug") and mine not in bm25)

    decoy = f"urn:kk:test:dense:decoy:{stamp}"
    submit(agent, "urn:kk:scope:default", decoy, "Doormat sales at the cat market rose; a doormat cat market report")
    embedded(agent, decoy)
    question = "Which animal is asleep on the doormat?"
    check("D-66 the cross-encoder is downloaded by the dense worker (KK_RERANK=1)", recall.reranker() is not None)
    got = [c["subject"] for c in recall.recall(agent, question, 5)]
    check("D-66 re-ranked recall puts the claim that answers the question first",
          got[:1] == [mine] and decoy in got, got[:3])
    os.environ["KK_RERANK"] = "0"
    check("D-66 KK_RERANK=0 keeps the fused order", recall.reranker() is None
          and [c["subject"] for c in recall.recall(agent, question, 5)] == hits(agent, question)[:5])
    del os.environ["KK_RERANK"]

    admin.execute("insert into kernel.scope(iri, label) values (%s, 'dense smoke: private') on conflict do nothing", (PRIVATE,))
    for role, write in (("person_owner", True), ("kkm_validator", False), ("kkm_belief", False),
                        ("kkm_recall_index", False), ("kkm_recall_dense", False)):
        admin.execute("""insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)
                         on conflict (scope, db_role) do update set can_read = true, can_write = excluded.can_write""",
                      (PRIVATE, role, write))
    secret = f"urn:kk:test:dense:private:{stamp}"
    submit(owner, PRIVATE, secret, "Quarterly revenue forecast for the lighthouse cooperative")
    check("D-61 the owner's claim of a private scope is embedded", embedded(owner, secret))
    check("D-61 an agent without that clearance neither finds it nor sees its embedding",
          secret not in hits(agent, "lighthouse cooperative revenue")
          and not agent.execute("select count(*) from recall_dense.vec where scope = %s", (PRIVATE,)).fetchone()[0])

    claim = agent.execute("select id from api.claims where subject = %s", (mine,)).fetchone()[0]
    run = owner.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "redact"}),)).fetchone()[0]
    owner.execute("select api.redact('claim', %s, 'dense smoke', %s)", (str(claim), run))
    deadline = time.time() + 30
    while time.time() < deadline and agent.execute("select count(*) from recall_dense.vec where claim_id = %s", (claim,)).fetchone()[0]:
        time.sleep(1)
    check("D-61 a redacted claim leaves the dense index",
          not agent.execute("select count(*) from recall_dense.vec where claim_id = %s", (claim,)).fetchone()[0])
    print(json.dumps({"embedded": agent.execute("select count(*) from recall_dense.vec").fetchone()[0]}))
    print(f"D-61, D-66 dense recall and re-ranking passed ({sum(checks)}/{len(checks)})" if all(checks) else "D-61, D-66 dense recall FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

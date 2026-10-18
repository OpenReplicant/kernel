"""M3-G3: the milestone-3 modules run sandboxed after `kk module install`, run by scripts/e2e_module.sh.

The paper encoder (a command) has stored and encoded a small JSON-lines file from its own container; the
recall index and the quote verifier run as workers in theirs. Checks: the papers' change sets are applied
as the encoder's own role; the recall index indexes them; the verifier, reading the artifact store
read-only, supports a quote of the stored file and refutes a made-up one; recall-dense installs (its
worker needs onnxruntime, which the Alpine kk image cannot hold, so it is not run here); M4-G2: the process
learner runs as a worker under its own login.

Env: KK_ADMIN_URL, KK_RESULT (the paper encoder's output line), KK_CHAT_RESULT (the interaction encoder's),
KK_PAPERS (the input file).
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def wait(conn, sql, args, ok, timeout=120):
    deadline = time.time() + timeout
    while True:
        got = conn.execute(sql, args).fetchone()
        if ok(got) or time.time() > deadline:
            return got
        time.sleep(1)


def main() -> int:
    res = json.loads(os.environ["KK_RESULT"].strip().splitlines()[-1])
    sha = hashlib.sha256(Path(os.environ["KK_PAPERS"]).read_bytes()).hexdigest()
    with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as conn:
        rows = [wait(conn, "select status, submitted_by from kernel.change_set where id = %s", (cs,),
                     lambda r: r[0] != "submitted") for cs in res["change_sets"]]
        check("M3-G3 the paper encoder runs sandboxed as itself and its change sets are applied",
              rows and all(r == ("applied", "kkm_encoder_papers") for r in rows), f"({rows}, {res['claims']} claims)")

        papers = "select count(*) from api.claims c where c.subject like 'urn:kk:paper:g3:%%'"
        indexed = papers.replace("select count(*) from api.claims c",
                                 "select count(*) from recall.doc d join api.claims c on c.id = d.claim_id")
        n = conn.execute(papers).fetchone()[0]
        got = wait(conn, indexed, (), lambda r: r[0] >= n > 0)[0]
        check("M3-G3 the recall index runs sandboxed and indexes the papers", got >= n > 0, f"({got} of {n})")

        # Two extracted claims quoting the stored file, one truthfully; as the encoder's role, so the
        # verifier is not checking its own output (invariant 5).
        conn.execute("set role kkm_encoder_papers")
        me = conn.execute("select kernel.my_agent()").fetchone()[0]
        run = conn.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "G3"}),)).fetchone()[0]
        stamp = str(int(time.time() * 1000))
        real, fake = f"urn:kk:test:g3:{stamp}:real", f"urn:kk:test:g3:{stamp}:fake"
        ops = [{"op": "add_claim", "subject": s, "predicate": "rdfs:label", "object": {"value": {"@value": s}},
                "evidence_type": "extracted", "references": [{"artifact": sha, "quote": q}]}
               for s, q in ((real, "Sandboxed workers read the artifact store without writing it"),
                            (fake, "Sandboxed workers rewrite the artifact store"))]
        conn.execute("select api.submit_change_set(%s)", (Jsonb({
            "idempotency_key": f"g3:{stamp}", "attributed_to": me, "generated_by": str(run),
            "target_scope": "urn:kk:scope:default", "ops": ops}),))
        conn.execute("reset role")
        q = """select v.verdict, cs.submitted_by from kernel.verification v join api.claims c on c.id = v.claim_id
               join kernel.change_set cs on cs.id = v.change_set where c.subject = %s"""
        got = {s: wait(conn, q, (s,), lambda r: r is not None) for s in (real, fake)}
        check("M3-G3 the quote verifier runs sandboxed, reads the artifact store and supports a real quote",
              got[real] == ("supported", "kkm_verifier_quotes"), got[real])
        check("M3-G3 ... and refutes a made-up one", got[fake] == ("refuted", "kkm_verifier_quotes"), got[fake])

        hybrid = conn.execute("select to_regclass('api.recall_hybrid') is not null or exists "
                              "(select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                              "where n.nspname = 'api' and p.proname = 'recall_hybrid')").fetchone()[0]
        check("M3-G3 recall-dense installs (its schema and api.recall_hybrid, with pgvector)", hybrid)
        learner = wait(conn, "select count(*) from pg_stat_activity where usename = 'kkm_learner_process'", (),
                       lambda r: r[0] > 0, timeout=60)[0]
        check("M4-G2 the process learner runs sandboxed as its own login", learner > 0, f"({learner} connection)")
        chat = json.loads(os.environ["KK_CHAT_RESULT"].strip().splitlines()[-1])
        rows = [wait(conn, "select status, submitted_by from kernel.change_set where id = %s", (cs,),
                     lambda r: r[0] != "submitted") for cs in chat["change_sets"]]
        check("M5-G3 the interaction encoder runs sandboxed as itself and its change sets are applied",
              rows and all(r == ("applied", "kkm_encoder_interaction") for r in rows) and chat["messages"] == 4,
              f"({rows}, {chat['claims']} claims)")
    print(f"M3-G3 milestone-3 modules passed ({sum(checks)}/{len(checks)})" if all(checks) else "M3-G3 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

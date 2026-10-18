"""M3-G2 (D-71): answers that cite the kernel are checked against the claims they cite, and eval scores
are claims. Run by scripts/smoke_kernel.sh while `kk serve` runs the `quotes` and `belief` parts.

Two papers are encoded (the paper encoder, as its role). The agent records what each says about a
proposition, citing the paper by its IRI with a quote, as an answer grounded in the kernel does: one
quote is in the paper's abstract, the other is made up. The quote verifier supports the first, naming
the abstract claim it found the quote in, and refutes the second; belief caps the refuted one. Then
the evaluator records an eval run file as DQV measurements, which the agent reads back; the same file
again changes nothing and a changed file supersedes the run's scores.

Env: DATABASE_URL (a login that may SET ROLE to the modules' roles), AGENT_DATABASE_URL, KK_ARTIFACTS;
the knowledge-kernel package installed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DQV = "http://www.w3.org/ns/dqv#"
CITO = "http://purl.org/spar/cito/"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def wait(db, sql, args, ok, timeout=90):
    deadline = time.time() + timeout
    while True:
        got = db.execute(sql, args).fetchone()
        if ok(got) or time.time() > deadline:
            return got
        time.sleep(1)


def applied(res):
    """Wait for the validator to decide an evaluator change set; its status (seen as the evaluator)."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
        db.execute("set role kkm_evaluator")
        return wait(db, "select status from api.change_sets where id = %s", (res["change_set"],),
                    lambda r: r and r[0] != "submitted")[0]


def command(*args, role):
    out = subprocess.run([sys.executable, "-m", *args], env={**os.environ, "KK_ROLE": role},
                         capture_output=True, text=True, check=True).stdout
    return [json.loads(l) for l in out.strip().splitlines()]


def main() -> int:
    stamp = str(int(time.time() * 1000))
    source = f"g2-{stamp}"
    work = Path(tempfile.mkdtemp())
    (work / "papers.jsonl").write_text("\n".join(json.dumps(p) for p in (
        {"_id": "1", "title": "Ward rounds and falls", "text": "Hourly ward rounds reduced falls by a third in a year-long trial."},
        {"_id": "2", "title": "A survey of rounding", "text": "Nurses reported that rounding took time from other care."})) + "\n")
    command("paper_encoder", "--source", source, str(work / "papers.jsonl"), role="kkm_encoder_papers")

    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    me = agent.execute("select kernel.my_agent()").fetchone()[0]
    run = agent.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "G2"}),)).fetchone()[0]
    prop = f"urn:kk:proposition:g2-{stamp}"
    p1, p2 = f"urn:kk:paper:{source}:1", f"urn:kk:paper:{source}:2"
    stance = lambda paper, rel, quote: {  # noqa: E731
        "op": "add_claim", "subject": paper, "predicate": CITO + rel, "object": {"entity": prop},
        "evidence_type": "extracted", "references": [{"source_uri": paper, "quote": quote}]}
    agent.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"g2:{stamp}", "attributed_to": me, "generated_by": str(run),
        "target_scope": "urn:kk:scope:default", "ops": [
            {"op": "add_claim", "subject": prop, "predicate": "rdfs:label",
             "object": {"value": {"@value": "Hourly rounding reduces falls"}}, "evidence_type": "testimony",
             "references": [{"source_uri": "urn:kk:test:g2"}]},
            stance(p1, "supports", "Hourly ward rounds reduced falls by a third"),
            stance(p2, "refutes", "Rounding had no effect on falls")]}),))

    q = """select v.verdict, v.details from api.verifications v join api.claims c on c.id = v.claim_id
           where c.subject = %s and c.predicate = %s and v.method = 'urn:kk:module:verifier-quotes'"""
    sup = wait(agent, q, (p1, CITO + "supports"), lambda r: r is not None)
    ref = wait(agent, q, (p2, CITO + "refutes"), lambda r: r is not None)
    abstract = agent.execute("select id::text from api.claims where subject = %s and predicate = %s",
                             (p1, "http://purl.org/dc/terms/abstract")).fetchone()[0]
    cited = sup and sup[1]["references"][0].get("claims", [])
    check("M3-G2 a quote of a paper the kernel holds is checked against its claims: supported, naming the abstract",
          sup and sup[0] == "supported" and abstract in cited, f"({sup and sup[0]}, cites {len(cited or [])} claims)")
    check("M3-G2 a quote the paper's claims do not contain is refuted", ref and ref[0] == "refuted")
    b = wait(agent, "select belief from api.claims where subject = %s and predicate = %s", (p2, CITO + "refutes"),
             lambda r: r and r[0] is not None and r[0] <= 0.1)
    check("M3-G2 belief caps the stance resting on a made-up quote (D-67)", b and b[0] is not None and b[0] <= 0.1, b)

    run_file = work / f"{stamp}-verify.json"
    scores = {"stage": "verify", "split": "dev", "models": {"some/model": 2},
              "claim_accuracy_with_kernel": 0.9, "claim_accuracy_closed_book": 0.75, "abstract_label_only": {"f1": 0.7}}
    run_file.write_text(json.dumps(scores))
    first = command("evaluator", "--suite", "g2", str(run_file), role="kkm_evaluator")[0]
    subject = f"urn:kk:eval:g2:{stamp}-verify"
    m = "select c.qualifiers->>%s, (c.object_value->>'@value')::float from api.claims c where c.subject = %s and c.predicate = %s and c.status <> 'deprecated'"
    status = applied(first)
    got = dict(agent.execute(m, (DQV + "isMeasurementOf", subject, DQV + "value")).fetchall())
    check("M3-G2 the evaluator records an eval run's scores as DQV measurements the agent can read",
          status == "applied" and got == {"urn:kk:metric:g2:claim_accuracy_with_kernel": 0.9,
                                                   "urn:kk:metric:g2:claim_accuracy_closed_book": 0.75,
                                                   "urn:kk:metric:g2:abstract_label_only.f1": 0.7}, got)
    again = command("evaluator", "--suite", "g2", str(run_file), role="kkm_evaluator")[0]
    check("M3-G2 the same run file again changes nothing", again["change_set"] == first["change_set"], again)
    run_file.write_text(json.dumps({**scores, "claim_accuracy_with_kernel": 0.95}))
    changed = command("evaluator", "--suite", "g2", str(run_file), role="kkm_evaluator")[0]
    status = applied(changed)
    got = dict(agent.execute(m, (DQV + "isMeasurementOf", subject, DQV + "value")).fetchall())
    check("M3-G2 a changed run file supersedes the run's scores",
          status == "applied" and got.get("urn:kk:metric:g2:claim_accuracy_with_kernel") == 0.95
          and len(got) == 3, (changed["superseded"], got))
    print(f"M3-G2 cited answers and scores as claims passed ({sum(checks)}/{len(checks)})" if all(checks)
          else "M3-G2 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

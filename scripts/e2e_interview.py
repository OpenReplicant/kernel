"""M5-T4: the interviewer on a live kernel, run by scripts/smoke_kernel.sh while `kk serve` runs (with the
quote verifier).

A scripted interview (modules/skill-interview/tests/fixtures): after each of the person's answers, the
interaction encoder records the conversation so far, the agent records what the answer says as extracted
claims quoting the message (as the interview skill says), and the map gap processor lists what the maps
still lack. Checks: each answer closes the gaps it was asked about and the gaps are the ones expected; at
the end the organisation chart and the process are the expected ones, with no gap left; every claim cites
the message it came from, and the quote verifier supports every quote.

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_interaction and kkm_map_gaps), AGENT_DATABASE_URL,
KK_ARTIFACTS; gitleaks on PATH (the ingest filter); the knowledge-kernel package installed.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/skill-interview/tests"))
from scripted import load  # noqa: E402

ORG = "http://www.w3.org/ns/org#"
NEXT = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#has_nextFlowNode"
DCT_TYPE = "http://purl.org/dc/terms/type"
METHOD = "urn:kk:module:verifier-quotes"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def module(args: list[str], role: str) -> dict:
    out = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, env={**os.environ, "KK_ROLE": role})
    if out.returncode:
        raise SystemExit(f"{args[0]} failed: {out.stderr[-1500:] or out.stdout}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def settled(role: str) -> None:
    """Wait until the validator has decided every change set `role` submitted (visible only to it)."""
    db = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    db.execute(f"set role {role}")
    for _ in range(240):
        if not db.execute("select count(*) from api.change_sets where status in ('submitted', 'valid')").fetchone()[0]:
            break
        time.sleep(0.25)
    db.close()


def short(g):
    return g[0].rsplit(":", 1)[1] + " " + g[1].rsplit(":", 1)[1]


def main() -> int:
    source = f"acme{int(time.time())}"
    messages, script, answers = load(source)
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    me = agent.execute("select kernel.my_agent()").fetchone()[0]
    mine = (script["organisation"] + ":%", script["process"] + ":%")
    chat = Path(tempfile.mkdtemp()) / "interview.jsonl"
    before, counts, ids = set(), [], []
    for a in answers:
        upto = [m for m in messages if int(m["id"]) <= int(a["id"])]
        chat.write_text("".join(json.dumps(m) + "\n" for m in upto))
        module(["interaction_encoder", str(chat), "--source", source], "kkm_encoder_interaction")
        settled("kkm_encoder_interaction")
        run = agent.execute("select api.start_run(null, null, '[]'::jsonb, %s)", (Jsonb({"purpose": "interview"}),)).fetchone()[0]
        cs = agent.execute("select api.submit_change_set(%s)", (Jsonb({
            "idempotency_key": f"interview:{script['organisation']}|{a['message']}", "attributed_to": me,
            "generated_by": str(run), "target_scope": "urn:kk:scope:default", "ops": a["ops"]}),)).fetchone()[0]
        for _ in range(240):
            status = agent.execute("select status, validation_report from api.change_sets where id = %s", (cs["id"],)).fetchone()
            if status[0] not in ("submitted", "valid"):
                break
            time.sleep(0.25)
        if status[0] != "applied":
            check(f"M5-T4 the agent's claims from message {a['id']} are applied", False, status)
            break
        ids.append(cs["id"])
        module(["map_gaps", "--once"], "kkm_map_gaps")
        settled("kkm_map_gaps")
        rows = agent.execute("""select g.subject, g.object_entity, t.object_entity, b.object_value->>'@value'
                                from api.claims g join api.claims t on t.subject = g.subject and t.predicate = %s
                                join api.claims b on b.subject = g.subject and b.predicate = %s
                                where g.predicate = %s and g.subject like 'urn:kk:gap:%%' and g.status <> 'deprecated'
                                  and t.status <> 'deprecated' and b.status <> 'deprecated'
                                  and (t.object_entity like %s or t.object_entity like %s)""",
                             ("http://www.w3.org/ns/oa#hasTarget", "http://www.w3.org/ns/oa#bodyValue", DCT_TYPE, *mine)).fetchall()
        now = {(k, t) for _, k, t, _ in rows}
        counts.append(len(now))
        closed = a["closes"] <= before and not (a["closes"] & now)
        check(f"M5-T4 after message {a['id']}: its asked gaps are closed and the gaps are the expected ones",
              closed and now == a["open"], sorted(short(g) for g in now))
        for *_, question in sorted(rows, key=lambda r: r[3])[:3]:
            print("      next:", question)
        before = now
    check("M5-T4 the gaps shrink as the person answers, to none", counts and counts[-1] == 0 and len(counts) == len(answers),
          counts)
    rel = lambda p: sorted((s.rsplit(":", 1)[1], o.rsplit(":", 1)[1]) for s, o in agent.execute(  # noqa: E731
        "select subject, object_entity from api.claims where predicate = %s and status <> 'deprecated' and subject like any(%s)",
        (p, ["urn:kk:person:acme:%", *mine])).fetchall())
    check("M5-T4 the organisation chart: who holds which post, and who reports to whom",
          rel(ORG + "holds") == [("dana-reyes", "procurement-lead"), ("lee-chen", "buyer"), ("sam-okafor", "finance-director")]
          and rel(ORG + "reportsTo") == [("buyer", "procurement-lead"), ("procurement-lead", "finance-director")],
          (rel(ORG + "holds"), rel(ORG + "reportsTo")))
    check("M5-T4 the process: start, check the budget code, raise a purchase order, end",
          rel(NEXT) == [("check-the-budget-code", "raise-a-purchase-order"), ("raise-a-purchase-order", "end"),
                        ("start", "check-the-budget-code")], rel(NEXT))
    refs = agent.execute("""select c.id, r.source_uri, r.quote from api.claims c join api.claim_references r on r.claim_id = c.id
                            where c.change_set = any(%s::uuid[])""", ([str(i) for i in ids],)).fetchall()
    total = sum(len(a["ops"]) for a in answers)
    check("M5-T4 every claim cites the message it came from, with the person's words",
          len(refs) == total and all(u.startswith(f"urn:kk:message:{source}:") and q for _, u, q in refs), f"{len(refs)}/{total}")
    verdicts = []
    for _ in range(120):
        verdicts = agent.execute("""select v.verdict, count(*) from api.verifications v join api.claims c on c.id = v.claim_id
                                    where c.change_set = any(%s::uuid[]) and v.method = %s group by 1""",
                                 ([str(i) for i in ids], METHOD)).fetchall()
        if sum(n for _, n in verdicts) >= total:
            break
        time.sleep(0.5)
    check("M5-T4 the quote verifier supports every quote against the recorded message", dict(verdicts) == {"supported": total},
          verdicts)
    print(f"M5-T4 interviewer passed ({sum(checks)}/{len(checks)})" if all(checks) else "M5-T4 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

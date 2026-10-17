"""Devops checks, run by scripts/e2e_devops.sh. Env: KK_ADMIN_URL (superuser), KK_CONTAINER (the observer),
OWNER_DATABASE_URL, AGENT_DATABASE_URL (the planning agent), KK_DC (the compose command for the stack)."""
import hashlib
import json
import os
import uuid
from pathlib import Path
import subprocess
import sys
import time

import psycopg

from kk.lib import run, submit

SOSA = "http://www.w3.org/ns/sosa/"
BBO = "https://www.irit.fr/recherches/MELODI/ontologies/BBO#"
OA = "http://www.w3.org/ns/oa#"
PPLAN = "http://purl.org/net/p-plan#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT_TYPE = "http://purl.org/dc/terms/type"
LAT = "urn:kk:property:latency-seconds-mean"
ROOT = Path(__file__).resolve().parents[1]
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def windows(conn, subject, prop):
    return conn.execute("""select valid_from, (object_value->>'@value')::float from api.claims
                           where subject = %s and predicate = %s and qualifiers->>%s = %s
                             and evidence_type = 'observed' order by valid_from""",
                        (subject, SOSA + "hasSimpleResult", SOSA + "observedProperty", prop)).fetchall()


with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as conn:
    rate = []
    for _ in range(90):
        rate = windows(conn, "urn:kk:node:shop", "urn:kk:property:request-rate")
        if len(rate) >= 3 and rate[-1][1] > 0:
            break
        time.sleep(1)
    check("M2-T5 windowed observations of the shop's request rate", len(rate) >= 3 and rate[-1][1] > 0,
          f"({len(rate)} windows, last {rate[-1][1] if rate else None}/s)")
    latency = windows(conn, "urn:kk:node:shop", "urn:kk:property:latency-seconds-mean")
    up = windows(conn, "urn:kk:node:prometheus", "urn:kk:property:replicas-up")
    check("M2-T5 every service observed: shop latency, Prometheus up", latency and up and up[-1][1] == 1,
          f"(latency {latency[-1][1] if latency else None} s)")
    starts = [r[0] for r in rate]
    check("M2-T5 one observation per window, windows aligned and distinct",
          len(set(starts)) == len(starts) and all(s.timestamp() % 10 == 0 for s in starts))
    node = conn.execute("""select count(*) from api.claims where subject = 'urn:kk:node:shop'
                           and predicate = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
                           and object_entity = 'https://w3id.org/knowledge-kernel/tosca#NodeTemplate'""").fetchone()[0]
    check("M2-T5 observations are about the self-model's node for the service", node >= 1)
    by = conn.execute("""select distinct s.submitted_by from api.claims c join api.change_sets s on s.id = c.change_set
                         where c.subject = 'urn:kk:node:shop' and c.evidence_type = 'observed'""").fetchall()
    check("M2-T5 recorded by the observer's own login", by == [("kkm_observer_prometheus",)], f"{by}")

probe = """import socket, sys
for host in ("prometheus", "shop", "example.com"):
    try:
        socket.create_connection((host, 9090 if host == "prometheus" else 8000 if host == "shop" else 443), 3).close()
        print(host, "reachable")
    except OSError:
        print(host, "unreachable")"""
seen = subprocess.run(["docker", "exec", os.environ["KK_CONTAINER"], "python", "-c", probe],
                      capture_output=True, text=True, timeout=60).stdout.split("\n")
check("D-52 the observer reaches Prometheus and nothing else (not the shop, not the internet)",
      "prometheus reachable" in seen and "shop unreachable" in seen and "example.com unreachable" in seen, f"{seen}")


def wait(fn, seconds, every=1.0):
    end = time.time() + seconds
    while True:
        got = fn()
        if got or time.time() > end:
            return got
        time.sleep(every)


def agent_submit(agent, me, key, ops, lane="review"):
    cs = {"idempotency_key": key, "attributed_to": me, "target_scope": "urn:kk:scope:default", "ops": ops, "lane": lane}
    with agent.cursor() as cur, run(cur, None, {"skill": "adaptation-planning"}) as (run_id, _):
        return str(submit(cur, cs, run_id)["id"])


def status(conn, cs):
    return wait(lambda: (lambda s: s if s != "submitted" else None)(
        conn.execute("select status from api.change_sets where id = %s", (cs,)).fetchone()[0]), 60, 0.5)


# ---------------------------------------------------------------- M2-T6: the MAPE-K loop
admin = psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True)
agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
me = agent.execute("select iri from kernel.agent where db_role = current_user").fetchone()[0]
# Knowledge: the written service level, recorded by the agent as an extracted claim with its quote.
slo = (ROOT / "deploy/demo/slo.md").read_text()
quote = "The shop answers requests within 200 ms on average"
limit_cs = agent_submit(agent, me, "slo:shop", [{
    "op": "add_claim", "subject": "urn:kk:node:shop", "predicate": BBO + "maxValue",
    "object": {"value": {"@value": 0.2, "@type": "http://www.w3.org/2001/XMLSchema#decimal"}},
    "qualifiers": {SOSA + "observedProperty": LAT}, "evidence_type": "extracted",
    "references": [{"source_uri": "file:deploy/demo/slo.md", "quote": quote}]}], lane="auto")
check("M2-T6 the shop's service level is recorded from its document", status(admin, limit_cs) == "applied" and quote in slo)

# Monitor + Analyse: stress the shop; the observer records the latency, the anomaly processor claims it.
subprocess.run(f"DEMO_RATE=40 {os.environ['KK_DC']} up -d loadgen", shell=True, check=True, capture_output=True)
anomaly = wait(lambda: admin.execute("""select t.subject from api.claims t join api.claims k on k.subject = t.subject
        where t.predicate = %s and t.object_entity = 'urn:kk:node:shop' and k.predicate = %s
          and k.object_entity = 'urn:kk:anomaly-kind:limit' and k.attributed_to = 'urn:kk:agent:anomaly'
        order by t.valid_from desc limit 1""", (OA + "hasTarget", DCT_TYPE)).fetchone(), 150, 2)
check("M2-T6 analyse: the stressed shop's latency is claimed as an anomaly", anomaly, f"{anomaly}")
if not anomaly:
    sys.exit("M2 devops FAILED (no anomaly)")
anomaly_claims = [str(r[0]) for r in admin.execute("select id from api.claims where subject = %s", anomaly).fetchall()]
premise_ev = {r[0] for r in admin.execute("""select distinct p.evidence_type from kernel.claim_derivation d
        join api.claims p on p.id = d.derived_from where d.claim_id = any(%s::uuid[])""", (anomaly_claims,)).fetchall()}
check("M2-T6 the anomaly is derived from the observation and the documented limit", premise_ev == {"observed", "extracted"},
      f"{sorted(premise_ev)}")

# Plan: the agent follows the adaptation-planning skill (scale the shop to 3), in the review lane.
plan = f"urn:kk:plan:{uuid.uuid4()}"
step = plan + ":step:1"
claim = lambda s, p, o: {"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": "inferred",
                         "derived_from": anomaly_claims}
plan_cs = agent_submit(agent, me, f"plan:{anomaly[0]}", [
    claim(plan, RDF_TYPE, {"entity": PPLAN + "Plan"}),
    claim(plan, "http://www.w3.org/2000/01/rdf-schema#label", {"value": {"@value": "Scale shop to 3 replicas (latency over limit)"}}),
    claim(step, RDF_TYPE, {"entity": SOSA + "Actuation"}), claim(step, PPLAN + "isStepOfPlan", {"entity": plan}),
    claim(step, DCT_TYPE, {"entity": "urn:kk:action:scale"}),
    claim(step, SOSA + "hasFeatureOfInterest", {"entity": "urn:kk:node:shop"}),
    claim(step, SOSA + "hasSimpleResult", {"value": {"@value": 3, "@type": "http://www.w3.org/2001/XMLSchema#integer"}})])
check("M2-T6 plan: the agent's scale-out waits for a person in the review lane", status(admin, plan_cs) == "awaiting_review")
# A step that skipped review is never carried out, even when applied.
rogue = f"urn:kk:plan:{uuid.uuid4()}:step:1"
agent_submit(agent, me, f"rogue:{rogue}", [
    claim(rogue, RDF_TYPE, {"entity": SOSA + "Actuation"}), claim(rogue, PPLAN + "isStepOfPlan", {"entity": rogue[:-7]}),
    claim(rogue, DCT_TYPE, {"entity": "urn:kk:action:restart"}),
    claim(rogue, SOSA + "hasFeatureOfInterest", {"entity": "urn:kk:node:shop"})], lane="auto")

# The person approves (the only manual step); Execute: the actuator scales through the socket proxy.
out = subprocess.run([sys.executable, "-m", "kk", "review", "approve", plan_cs, "--note", "e2e: scale out"],
                     capture_output=True, text=True, env={**os.environ, "KK_DATABASE_URL": os.environ["OWNER_DATABASE_URL"]})
check("M2-T6 a person approves the plan", status(admin, plan_cs) == "applied", (out.stdout + out.stderr).strip()[-200:])
done = wait(lambda: admin.execute("""select status, used, cost, ended_at from kernel.run
        where agent = 'urn:kk:agent:actuator-compose' and plan_step = %s and status <> 'running'""", (step,)).fetchone(), 90)
plan_claims = {str(r[0]) for r in admin.execute("select id from api.claims where change_set = %s and subject = %s",
                                                 (plan_cs, step)).fetchall()}
check("M2-T6 execute: the actuator carries out the approved step, recorded as a run using the step's claims",
      done and done[0] == "succeeded" and set(done[1]) == plan_claims and done[2].get("after") == 3, f"{done and done[:3]}")
running = subprocess.run(f"{os.environ['KK_DC']} ps -q shop", shell=True, capture_output=True, text=True).stdout.split()
check("M2-T6 three shop replicas run", len(running) == 3, f"{len(running)}")
claims_by_actuator = admin.execute("select count(*) from api.claims where attributed_to = 'urn:kk:agent:actuator-compose'").fetchone()[0]
rogue_runs = admin.execute("select count(*) from kernel.run where plan_step = %s", (rogue,)).fetchone()[0]
check("M2-T6 the actuator writes no claims and ignores a step that skipped review", claims_by_actuator == 0 and rogue_runs == 0)

# Monitor again: the observer records three replicas up and the latency back within the limit.
ended = done[3] if done else None
recovered = wait(lambda: admin.execute("""select r.valid_from, l.object_value->>'@value' from api.claims r join api.claims l
        on l.subject = r.subject and l.valid_from = r.valid_from and l.qualifiers->>%s = %s
        where r.subject = 'urn:kk:node:shop' and r.qualifiers->>%s = 'urn:kk:property:replicas-up'
          and (r.object_value->>'@value')::float = 3 and (l.object_value->>'@value')::float < 0.2
          and r.valid_from >= %s order by r.valid_from limit 1""",
        (SOSA + "observedProperty", LAT, SOSA + "observedProperty", ended)).fetchone(), 150, 2) if ended else None
check("M2-T6 the effect is observed: three replicas up, latency back under the limit", recovered, f"{recovered}")
print("M2 devops", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
sys.exit(0 if all(checks) else 1)

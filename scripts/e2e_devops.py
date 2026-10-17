"""Devops checks, run by scripts/e2e_devops.sh. Env: KK_ADMIN_URL (superuser), KK_CONTAINER (the observer)."""
import os
import subprocess
import sys
import time

import psycopg

SOSA = "http://www.w3.org/ns/sosa/"
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
print("M2 devops", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
sys.exit(0 if all(checks) else 1)

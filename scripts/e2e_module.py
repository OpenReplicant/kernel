"""T18 checks, run by scripts/e2e_module.sh. Env: KK_ADMIN_URL (superuser), KK_CONTAINER (the module)."""
import json
import os
import subprocess
import sys
import time

import psycopg

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def inside(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", os.environ["KK_CONTAINER"], "python", "-c", code],
                          capture_output=True, text=True, timeout=60)


def inspect() -> dict:
    out = subprocess.run(["docker", "inspect", os.environ["KK_CONTAINER"]], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)[0]


with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as conn:
    scored = 0
    for _ in range(120):  # the installed packs alone are ~440 claims to score
        scored = conn.execute("select count(distinct b.claim_id) from kernel.belief b join kernel.run r"
                              " on r.id = b.computed_by where r.agent = 'urn:kk:agent:belief-corroboration'"
                              " and r.started_at > now() - interval '10 minutes'").fetchone()[0]
        if scored:
            break
        time.sleep(1)
    check("the installed belief module scores claims from its own container", scored > 0, f"{scored} claims")
    logins = conn.execute("select rolcanlogin from pg_roles where rolname = 'kkm_belief'").fetchone()
    check("its role has its own login", logins == (True,))
    sessions = conn.execute("select count(*) from pg_stat_activity where usename = 'kkm_belief'").fetchone()[0]
    check("it is connected as its own login, not the services' login", sessions > 0, f"{sessions} sessions")

info = inspect()
host = info["HostConfig"]
check("read-only filesystem", host["ReadonlyRootfs"] is True)
check("all capabilities dropped", host["CapDrop"] == ["ALL"] or "ALL" in (host["CapDrop"] or []), str(host["CapDrop"]))
check("no new privileges", any("no-new-privileges" in o for o in host["SecurityOpt"] or []))
check("runs as nobody", info["Config"]["User"] == "nobody")
nets = list(info["NetworkSettings"]["Networks"])
check("only the internal network to Postgres", len(nets) == 1 and nets[0].endswith("kernel-db"), str(nets))
r = inside("open('/kk/x', 'w')")
check("cannot write its filesystem", r.returncode != 0 and "Read-only" in r.stderr, r.stderr.strip()[-60:])
r = inside("import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)")
check("no route out", r.returncode != 0, r.stderr.strip().splitlines()[-1][:80] if r.stderr else "")
r = inside("import os, psycopg; c = psycopg.connect(os.environ['DATABASE_URL']); c.execute('set role kkm_validator')")
check("its login cannot act as another module", r.returncode != 0 and "permission denied" in r.stderr,
      r.stderr.strip().splitlines()[-1][:80] if r.stderr else "")

print("T18 module install", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
sys.exit(0 if all(checks) else 1)

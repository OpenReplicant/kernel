"""The validator against a throwaway kernel (D-111, O-9): a change set whose apply waits on a row another transaction
holds times out, is left submitted and tried again later; the change sets behind it are judged in the same pass.

An agent holds an uncommitted insert of an entity (agents may insert entity identifiers) that one change set names.
The validator's pass runs with a short lock timeout: the innocent change sets apply, the stuck one stays submitted
and does not stop the pass. Once the agent lets go, the next pass applies it.

Needs Postgres 16+ (PGHOST/PGUSER as for psql; a superuser) and this repository installed (`pip install -e .`).
Skips (exit 0) when one is missing. Drops its database at the end.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable


def skip(why: str):
    print(f"skip: {why}")
    sys.exit(0)


try:
    import psycopg
    from kk.lib import connect, run, submit
    from validator.__main__ import drain
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")
try:
    admin = psycopg.connect(dbname=os.environ.get("PGDATABASE", "postgres"), autocommit=True)
    if not admin.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:
        skip("needs a superuser")
except psycopg.Error as e:
    skip(f"no Postgres: {str(e).strip()[:100]}")

DB = f"vd_kernel_{os.getpid()}"
PW = secrets.token_hex(12)
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")
LABEL = "http://www.w3.org/2000/01/rdf-schema#seeAlso"
checks, fails = 0, []


def check(name: str, ok: bool, detail: str = ""):
    global checks
    checks += 1
    if not ok:
        fails.append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def url(user: str) -> str:
    if HOST.startswith("/"):
        return f"postgresql://{user}:{PW}@/{DB}?host={HOST}&port={PORT}"
    return f"postgresql://{user}:{PW}@{HOST}:{PORT}/{DB}"


def main():
    admin.execute(f'create database "{DB}"')
    kenv = {**os.environ, "KK_ADMIN_URL": f"dbname={DB}", "KK_AUTHENTICATOR_PASSWORD": PW,
            "KK_JWT_SECRET": secrets.token_hex(24), "DATABASE_URL": url("kk_authenticator")}
    subprocess.run([PY, "-m", "kk", "init"], check=True, env=kenv, cwd=ROOT, capture_output=True)
    kadmin = psycopg.connect(dbname=DB, autocommit=True)

    def status(cs: str) -> str:
        return kadmin.execute("select status from kernel.change_set where id = %s", (cs,)).fetchone()[0]

    agent = connect("kkm_encoder_substrate", url=url("kk_authenticator"))

    def say(key: str, obj: str) -> str:
        with agent.cursor() as cur, run(cur, "urn:kk:module:encoder-substrate") as (rid, _):
            res = submit(cur, {"idempotency_key": key, "attributed_to": "urn:kk:agent:encoder-substrate",
                               "target_scope": "urn:kk:scope:default",
                               "ops": [{"op": "add_claim", "subject": f"urn:kk:test:{key}", "predicate": LABEL,
                                        "object": {"entity": obj}, "evidence_type": "deterministic",
                                        "references": [{"source_uri": "https://example.org/drain"}]}]}, rid)
        return res["id"]

    stuck = say("drain:stuck", "urn:kk:test:held")
    after = [say(f"drain:after{i}", f"urn:kk:test:free{i}") for i in range(2)]

    holder = connect("kkm_encoder_substrate", url=url("kk_authenticator"))
    holder.execute("begin")
    holder.execute("insert into kernel.entity (iri) values ('urn:kk:test:held')")

    validator = connect("kkm_validator", url=url("kk_authenticator"))
    validator.execute("set lock_timeout = '500ms'")
    judged = drain(validator)
    check("the pass judges the change sets behind the stuck one", judged == 2 and all(status(c) == "applied" for c in after),
          f"judged {judged}, {[status(c) for c in after]}")
    check("the stuck one is left submitted, not invalid", status(stuck) == "submitted", status(stuck))

    holder.execute("rollback")
    judged = drain(validator)
    check("once the row is free, the next pass applies it", judged == 1 and status(stuck) == "applied", f"{judged} {status(stuck)}")
    check("and the pass after that has nothing to do", drain(validator) == 0)
    for c in (agent, holder, validator, kadmin):
        c.close()


try:
    main()
finally:
    admin.execute(f'drop database if exists "{DB}" with (force)')
if fails:
    sys.exit(f"FAIL: {len(fails)} of {checks}: {fails}")
print(f"D-111 validator retry checks passed ({checks}/{checks})")

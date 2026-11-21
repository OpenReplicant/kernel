"""A throwaway kernel for the detector tests: its database, a validator that applies change sets, helpers.

Needs Postgres 16+ (PGHOST/PGUSER/PGDATABASE as for psql; a superuser) and this repository installed
(`pip install -e .`). `skip()` exits 0 when one is missing."""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")


def skip(why: str):
    print(f"skip: {why}")
    sys.exit(0)


try:
    import psycopg
    from psycopg.rows import dict_row
    from kk.lib import connect, run, submit  # noqa: F401
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")
try:
    admin = psycopg.connect(dbname=os.environ.get("PGDATABASE", "postgres"), autocommit=True)
    if not admin.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:
        skip("needs a superuser")
except psycopg.Error as e:
    skip(f"no Postgres: {str(e).strip()[:100]}")


class Kernel:
    def __init__(self, tag: str):
        self.db = f"{tag}_kernel_{os.getpid()}"
        self.pw = secrets.token_hex(12)
        self.procs, self.checks, self.fails = [], 0, []

    def url(self, user: str) -> str:
        if HOST.startswith("/"):
            return f"postgresql://{user}:{self.pw}@/{self.db}?host={HOST}&port={PORT}"
        return f"postgresql://{user}:{self.pw}@{HOST}:{PORT}/{self.db}"

    def start(self):
        admin.execute(f'create database "{self.db}"')
        env = {**os.environ, "KK_ADMIN_URL": f"dbname={self.db}", "KK_AUTHENTICATOR_PASSWORD": self.pw,
               "KK_JWT_SECRET": secrets.token_hex(24), "DATABASE_URL": self.url("kk_authenticator")}
        os.environ["DATABASE_URL"] = env["DATABASE_URL"]     # kk.lib.connect(role) reads it
        subprocess.run([PY, "-m", "kk", "init"], check=True, env=env, cwd=ROOT, capture_output=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.procs.append(subprocess.Popen([PY, "-m", "kk", "serve", "--only", "validator", "--port", str(port)],
                                           env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.k = psycopg.connect(dbname=self.db, autocommit=True, row_factory=dict_row)   # the superuser's view

    def stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        admin.execute(f'drop database if exists "{self.db}" with (force)')

    def check(self, name: str, ok: bool, detail: str = ""):
        self.checks += 1
        if not ok:
            self.fails.append(name)
        print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

    def wait_applied(self):
        for _ in range(120):
            n = self.k.execute("select count(*) as n from kernel.change_set where status not in "
                               "('applied', 'invalid', 'rejected', 'conflict')").fetchone()["n"]
            if n == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("change sets did not apply: is the validator running?")

    def say(self, ops: list[dict], scope: str = "urn:kk:scope:default", role: str = "kkm_encoder_substrate",
            agent: str = "urn:kk:agent:encoder-substrate") -> dict:
        with connect(role) as c, c.cursor() as cur:
            with run(cur, "urn:kk:module:" + agent.rsplit(":", 1)[1]) as (rid, _):
                res = submit(cur, {"idempotency_key": f"t:{secrets.token_hex(6)}", "attributed_to": agent,
                                   "target_scope": scope, "ops": ops}, rid)
        self.wait_applied()
        return res

    def seq(self) -> int:
        return self.k.execute("select coalesce(max(seq), 0) as n from kernel.applied").fetchone()["n"]

    def events(self, since: int, detector: str | None = None) -> list[tuple]:
        """(detector, kind, key) of the events applied after `since`, in order."""
        rows = self.k.execute(
            "select d.object_entity as det, k.object_entity as kind, l.object_value->>'@value' as key "
            "from kernel.claim i "
            "join kernel.claim d on d.id = i.id "
            "join kernel.claim k on k.subject = i.subject and k.predicate = 'http://purl.org/dc/terms/type' "
            "join kernel.claim u on u.subject = i.subject and u.predicate = 'http://www.w3.org/ns/prov#used' "
            "join kernel.claim l on l.subject = u.object_entity and l.predicate = 'http://www.w3.org/2000/01/rdf-schema#label' "
            "join kernel.applied a on a.change_set = i.change_set "
            "where i.predicate = 'https://www.w3.org/ns/activitystreams#instrument' and a.seq > %s "
            "order by a.seq, k.object_entity, l.object_value->>'@value'", (since,)).fetchall()
        return [(r["det"].rsplit(":", 1)[1], r["kind"].rsplit(":", 1)[1], r["key"]) for r in rows
                if detector in (None, r["det"].rsplit(":", 1)[1])]

    def finish(self, what: str):
        if self.fails:
            sys.exit(f"FAIL: {len(self.fails)} of {self.checks}: {self.fails}")
        print(f"{what} ({self.checks}/{self.checks})")

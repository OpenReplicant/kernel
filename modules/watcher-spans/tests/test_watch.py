#!/usr/bin/env python3
"""The span watcher against a throwaway kernel (D-106).

Makes a kernel (`kk init`, `kk serve --only validator`), has an encoder's role submit claims that cite file
spans (the shape encoder-substrate gives them), and runs the watcher's pass over a temporary tree:
unchanged -> nothing submitted; a line moved -> the claims citing it flagged, with where it went; running
again -> nothing; the file put back -> restored; a line edited -> flagged; a file deleted -> flagged; a claim
in a private scope the watcher is not cleared for -> not seen, so not flagged, until it is cleared.
Drops the database and stops the server at the end.

Needs Postgres 16+ (PGHOST/PGUSER/PGDATABASE as for psql; a superuser) and this repository installed
(`pip install -e .`). Skips (exit 0) when one is missing.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable


def skip(why: str):
    print(f"skip: {why}")
    sys.exit(0)


try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb  # noqa: F401
    from kk.lib import connect, run, submit
    from watcher_spans.__main__ import METHOD, Pass, change_sets
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")
try:
    admin = psycopg.connect(dbname=os.environ.get("PGDATABASE", "postgres"), autocommit=True)
    if not admin.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:
        skip("needs a superuser")
except psycopg.Error as e:
    skip(f"no Postgres: {str(e).strip()[:100]}")

DB = f"ws_kernel_{os.getpid()}"
PW = secrets.token_hex(12)
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")
checks, fails, procs = 0, [], []
TERM = "http://purl.org/dc/terms/description"


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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cleanup():
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    admin.execute(f'drop database if exists "{DB}" with (force)')


def claim(n: int, source: str, locator: str, quote: str) -> dict:
    """What encoder-substrate makes of a store statement cited at a file span."""
    return {"op": "add_claim", "subject": f"urn:kk:substrate:Qt{n}", "predicate": TERM,
            "object": {"value": {"@value": f"claim {n}"}}, "evidence_type": "deterministic",
            "references": [{"source_uri": f"urn:kk:substrate:reference:r{n}?by=parser:test", "quote": quote,
                            "span": {"type": "FragmentSelector", "value": locator, "source": source}}]}


def main():
    admin.execute(f'create database "{DB}"')
    kenv = {**os.environ, "KK_ADMIN_URL": f"dbname={DB}", "KK_AUTHENTICATOR_PASSWORD": PW,
            "KK_JWT_SECRET": secrets.token_hex(24), "DATABASE_URL": url("kk_authenticator")}
    subprocess.run([PY, "-m", "kk", "init"], check=True, env=kenv, cwd=ROOT, capture_output=True)
    procs.append(subprocess.Popen([PY, "-m", "kk", "serve", "--only", "validator", "--port", str(free_port())],
                                  env=kenv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    kadmin = psycopg.connect(dbname=DB, autocommit=True, row_factory=dict_row)

    def wait_applied():
        for _ in range(120):
            n = kadmin.execute("select count(*) as n from kernel.change_set where status not in "
                               "('applied', 'invalid', 'rejected', 'conflict')").fetchone()["n"]
            if n == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("change sets did not apply: is the validator running?")

    def say(ops: list[dict], scope: str = "urn:kk:scope:default") -> None:
        with connect("kkm_encoder_substrate", url=url("kk_authenticator")) as c, c.cursor() as cur:
            with run(cur, "urn:kk:module:encoder-substrate") as (rid, _):
                res = submit(cur, {"idempotency_key": f"t:{secrets.token_hex(6)}", "attributed_to": "urn:kk:agent:encoder-substrate",
                                   "target_scope": scope, "ops": ops}, rid)
        wait_applied()
        check(f"claims submitted: {len(ops)} in {scope.rsplit(':', 1)[1]}", res["status"] in ("applied", "pending", "submitted"), str(res))

    def verdicts() -> dict:
        rows = kadmin.execute("select s.object_value->>'@value' as who, v.verdict, v.details from kernel.verification v "
                              "join kernel.claim s on s.id = v.claim_id where v.method = %s order by v.recorded_at", (METHOD,)).fetchall()
        latest = {}
        for r in rows:
            latest[r["who"]] = (r["verdict"], r["details"])
        return latest

    watcher = connect("kkm_watcher_spans", url=url("kk_authenticator"))

    def sets(roots):
        with watcher.cursor() as cur:
            return change_sets(cur, roots)

    def sweep(roots):
        Pass(roots)(watcher)
        wait_applied()
        return verdicts()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        parts, stable, gone = root / "parts.txt", root / "stable.txt", root / "gone.txt"
        parts.write_text("alpha\nthe chain fits 1/2 x 3/32\nomega\n")
        stable.write_text("steady line\n")
        gone.write_text("this file will go\n")
        roots = [root]
        say([claim(1, "file:parts.txt", "L2", "the chain fits 1/2 x 3/32"),
             claim(2, "file:stable.txt", "L1", "steady line"),
             claim(3, "file:parts.txt", "L3", "omega"),
             claim(4, "file:gone.txt", "L1", "this file will go"),
             claim(5, "file:parts.txt", "L2", "the chain fits 1/2 x 3/32"),       # a second claim citing the same span
             claim(6, "file:parts.txt", "p3", "not a line range")])
        check("the watcher sees the claims through api.claim_references", kadmin.execute("select count(*) as n from kernel.claim where predicate = %s", (TERM,)).fetchone()["n"] == 6)

        check("unchanged: nothing is submitted", sets(roots) == [] and verdicts() == {})
        check("unchanged: a pass records no verdict", sweep(roots) == {})

        parts.write_text("new first line\nalpha\nthe chain fits 1/2 x 3/32\nomega\n")
        v = sweep(roots)
        check("a line moved: the claims citing it are flagged, the others not",
              {k for k, x in v.items() if x[0] == "inconclusive"} == {"claim 1", "claim 3", "claim 5"}, str(v.keys()))
        ch = {k: x[1]["changed"][0] for k, x in v.items()}
        check("a moved line is reported with where it went", ch["claim 1"]["why"] == "moved" and ch["claim 1"]["moved_to"] == "L3"
              and ch["claim 3"]["moved_to"] == "L4", str(ch))
        check("the verdict names the span and what was pinned", ch["claim 1"]["source"] == "file:parts.txt"
              and ch["claim 1"]["locator"] == "L2" and len(ch["claim 1"]["pinned_sha256"]) == 64)
        check("running again with nothing changed submits nothing", sets(roots) == [])
        n_before = kadmin.execute("select count(*) as n from kernel.verification").fetchone()["n"]
        sweep(roots)
        check("and records nothing", kadmin.execute("select count(*) as n from kernel.verification").fetchone()["n"] == n_before)
        check("nothing was written but verifications: no claim deprecated, none added",
              kadmin.execute("select count(*) as n from kernel.claim where predicate = %s", (TERM,)).fetchone()["n"] == 6)

        parts.write_text("alpha\nthe chain fits 1/2 x 3/32\nomega\n")
        v = sweep(roots)
        check("the file put back: the claims are restored", all(v[k][0] == "supported" for k in ("claim 1", "claim 3", "claim 5")), str(v))

        parts.write_text("alpha\nthe chain fits 1/8 inch\nomega\n")
        v = sweep(roots)
        check("a line edited: the claims citing it are flagged again as changed, the one on another line is not",
              v["claim 1"][0] == "inconclusive" and v["claim 1"][1]["changed"][0]["why"] == "changed" and v["claim 5"][0] == "inconclusive"
              and v["claim 3"][0] == "supported", str(v))
        check("a locator that is not a line range is never flagged", "claim 6" not in v)

        gone.unlink()
        v = sweep(roots)
        check("a deleted file: the claim citing it is flagged gone", v["claim 4"][0] == "inconclusive"
              and v["claim 4"][1]["changed"][0]["why"] == "gone", str(v.get("claim 4")))
        check("and the claim on the file that is still there is not", "claim 2" not in v)

        # a second checkout the same source resolves in
        other = Path(tempfile.mkdtemp())
        (other / "gone.txt").write_text("this file will go\n")
        v = sweep([root, other])
        check("a file found in another of the source roots: the claim is restored", v["claim 4"][0] == "supported", str(v.get("claim 4")))

        # row-level security: a private claim on a changed span
        scope = "urn:kk:scope:ws-private"
        kadmin.execute("insert into kernel.scope(iri, label) values (%s, 'span watcher test: private')", (scope,))
        for role, w in (("kkm_encoder_substrate", True), ("kkm_validator", False)):
            kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (scope, role, w))
        say([claim(7, "file:stable.txt", "L1", "a line that is not there")], scope)
        v = sweep([root, other])
        check("rls: a claim in a scope the watcher is not cleared for is not seen, so not flagged", "claim 7" not in v, str(v.get("claim 7")))
        kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, 'kkm_watcher_spans', true, true)", (scope,))
        v = sweep([root, other])
        check("rls: cleared for it, the watcher flags it, in that scope",
              v.get("claim 7", ("",))[0] == "inconclusive" and kadmin.execute(
                  "select scope from kernel.claim c join kernel.verification v on v.claim_id = c.id where c.object_value->>'@value' = 'claim 7' "
                  "and v.method = %s", (METHOD,)).fetchone()["scope"] == scope)
        kadmin.execute("delete from kernel.scope_grant where scope = %s and db_role = 'kkm_watcher_spans'", (scope,))
        check("rls: and with the grant taken away it plans nothing for it", all(op["claim"] for cs in sets([root, other]) for op in cs["ops"]) and
              not any(cs["target_scope"] == scope for cs in sets([root, other])))

        check("every verification is by the watcher's method and agent", kadmin.execute(
            "select count(*) as n from kernel.verification v join kernel.run r on r.id = v.run "
            "where v.method = %s and r.agent <> 'urn:kk:agent:watcher-spans'", (METHOD,)).fetchone()["n"] == 0)


try:
    main()
finally:
    cleanup()
if fails:
    sys.exit(f"FAIL: {len(fails)} of {checks}: {fails}")
print(f"D-106 span watcher database checks passed ({checks}/{checks})")

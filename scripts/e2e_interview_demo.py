"""M5-T5 (D-117): `kk demo --interview` as a customer sees it, run by scripts/smoke_kernel.sh (needs Node and
playwright; the Map view is the point). A fresh database and its own `kk serve` (with the quote verifier and the gap
processor), as scripts/e2e_demo.py has them.

`kk demo --interview --fast` plays the scripted conversation; running it again changes nothing. Then
scripts/e2e_interview_demo.mjs signs in and checks in headless Chromium that both maps are filled, that a person
corrects one thing in place and confirms one proposal, and that a typed answer is recorded in the chat pane.

Env: DATABASE_URL (kk_authenticator; its database is replaced), KK_JWT_SECRET, PG* (a superuser); gitleaks on PATH;
SHOT_DIR (optional screenshot folder)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def sh(*cmd, env=None, cwd=None) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"{' '.join(cmd[:4])} failed: {(out.stderr or out.stdout)[-1500:]}")
    return out


def objects(text: str) -> list[dict]:
    dec, i, out = json.JSONDecoder(), 0, []
    while (i := text.find("{", i)) >= 0:
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


def up(base: str) -> bool:
    try:
        urllib.request.urlopen(base + "/ui/views.json", timeout=5).close()
        return True
    except OSError:
        return False


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = f"kk_interview_{int(time.time())}"
        env = {**os.environ, "KK_HOME": f"{tmp}/kk-home", "KK_ADMIN_URL": f"dbname={db}", "KK_ARTIFACTS": f"{tmp}/artifacts",
               "KK_CHATS": f"{tmp}/chats", "KK_SWEEP_SECONDS": "3"}
        sh("createdb", db)
        sh(sys.executable, "-m", "kk", "init", env=env)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        serve_url = urlunparse(urlparse(os.environ["DATABASE_URL"])._replace(path="/" + db))
        log = open(f"{tmp}/serve.log", "w")
        serve = subprocess.Popen([sys.executable, "-m", "kk", "serve", "--port", str(port), "--only", "mcp,validator,belief,recall,quotes,gaps"],
                                 env={**env, "DATABASE_URL": serve_url}, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                if serve.poll() is not None or up(base):
                    break
                time.sleep(1)
            if serve.poll() is not None:
                raise SystemExit(f"kk serve did not start: {Path(log.name).read_text()[-1500:]}")
            host = f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"
            demo = [sys.executable, "-m", "kk", "demo", "--interview", "--fast", "--project", f"{tmp}/kk-demo", "--agent", "demo",
                    "--no-stack", "--db-host", host]
            first = objects(sh(*demo, env=env).stdout)
            played = first[-1]["played"]
            counts = lambda: sh("psql", "-d", db, "-tAc", "select (select count(*) from api.claims), (select count(*) from api.change_sets)").stdout.strip()  # noqa: E731
            before = counts()
            while True:   # the belief model scores a moment after the apply: take the count once it is still
                time.sleep(4)
                if counts() == before:
                    break
                before = counts()
            check("M5-T5 kk demo --interview plays the scripted conversation: five answers, the claims they hold",
                  played["said"] == 5 and played["claims"] >= 40 and first[0].get("owner_sign_in"), json.dumps(played))
            again = objects(sh(*demo, env=env).stdout)[-1]["played"]
            check("M5-T5 kk demo --interview again changes nothing", again["said"] == 0 and again["claims"] == 0 and counts() == before,
                  f"{before} -> {counts()}")
            sh(sys.executable, "-m", "kk", "person", "add", "editor", "--steward", env=env)
            token = lambda role: sh(sys.executable, "-m", "kk", "token", role, "--days", "0.01", env={**env, "KK_JWT_SECRET": os.environ["KK_JWT_SECRET"]}).stdout.strip()  # noqa: E731
            if not subprocess.run(["node", "-e", "require('playwright')"], cwd=ROOT, capture_output=True).returncode == 0:
                if os.environ.get("KK_REQUIRE_UI") == "1":
                    check("M5-T5 the headless UI check needs Node and playwright", False)
                else:
                    print("skip the interview demo UI check (no playwright)")
            else:
                out = subprocess.run(["node", "scripts/e2e_interview_demo.mjs"], cwd=ROOT, env={
                    **env, "KK_URL": base, "KK_OWNER_TOKEN": token("person_owner"), "KK_EDITOR_TOKEN": token("person_editor"),
                    "PYTHON": sys.executable, "DB": db})
                check("M5-T5 the interview fills both maps in the UI; a person corrects and confirms in place", out.returncode == 0)
        finally:
            serve.terminate()
            time.sleep(1)
            if not os.environ.get("KK_KEEP_DB"):
                subprocess.run(["dropdb", "--if-exists", "--force", db], capture_output=True)
    print(f"M5-T5 the interview demo passed ({sum(checks)}/{len(checks)})" if all(checks) else "M5-T5 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

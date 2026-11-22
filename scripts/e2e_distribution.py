"""M5-T9 (D-118): distributions, run by scripts/smoke_kernel.sh (no Docker).

A fresh database. `kk up --distribution business --no-stack` connects a project; running it again changes nothing.
`kk demo --distribution business --fast` loads the interview data, and again changes nothing. A `kk serve` with
KK_DISTRIBUTION=business lists only that distribution's views, where a plain one lists all. `kk distribution list`
names the four distributions. A broken distribution file fails scripts/check_manifests.py. Needs a superuser (PG*)
and DATABASE_URL (kk_authenticator) as scripts/e2e_interview_demo.py does."""
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_interview_demo import ROOT, check, checks, objects, sh  # noqa: E402


def views(base: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(base + "/ui/views.json", timeout=5) as r:
            return sorted(v["name"] for v in json.load(r))
    except OSError:
        return None


def serve(db: str, env: dict, parts: str, tmp: str, tag: str):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = urlunparse(urlparse(os.environ["DATABASE_URL"])._replace(path="/" + db))
    p = subprocess.Popen([sys.executable, "-m", "kk", "serve", "--port", str(port), "--only", parts],
                         env={**env, "DATABASE_URL": url}, stdout=open(f"{tmp}/{tag}.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if p.poll() is not None or views(base) is not None:
            break
        time.sleep(1)
    return p, base


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = f"kk_dist_{int(time.time())}"
        env = {**os.environ, "KK_HOME": f"{tmp}/kk-home", "KK_ADMIN_URL": f"dbname={db}", "KK_ARTIFACTS": f"{tmp}/artifacts",
               "KK_CHATS": f"{tmp}/chats"}
        host = f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"
        kk = lambda *a, e=env: sh(sys.executable, "-m", "kk", *a, env=e)  # noqa: E731
        listing = kk("distribution", "list").stdout
        check("M5-T9 kk distribution list names business, process-maps, devops and papers",
              all(n in listing for n in ("business", "process-maps", "devops", "papers")))
        bad = Path(tmp) / "broken"
        bad.mkdir()
        (bad / "distribution.yaml").write_text("name: broken\ntitle: x\ndoes: y\nmodules: [no-such-module]\npacks: []\nskills: []\n"
                                                "views: []\ndemo: none\n")
        r = subprocess.run([sys.executable, "scripts/check_manifests.py", str(bad / "distribution.yaml")], cwd=ROOT, capture_output=True, text=True)
        check("M5-T9 check_manifests fails on a distribution naming a module that does not exist",
              r.returncode == 1 and "no-such-module" in r.stdout)
        sh("createdb", db)
        kk("init")
        project = f"{tmp}/proj"
        up = ["up", project, "--distribution", "business", "--no-stack", "--db-host", host]
        Path(project).mkdir()
        first = objects(kk(*up).stdout)[0]
        second = objects(kk(*up).stdout)[0]
        check("M5-T9 kk up --distribution business connects the project", first["changed"] and first["owner_sign_in"])
        check("M5-T9 kk up --distribution business again changes nothing", second["changed"] == [], json.dumps(second["changed"]))
        p, base = serve(db, {**env, "KK_DISTRIBUTION": "business"}, "mcp,validator,belief,recall,quotes,gaps", tmp, "business")
        q, base_all = serve(db, env, "mcp", tmp, "plain")
        demo = ["demo", "--distribution", "business", "--fast", "--project", f"{tmp}/kk-demo", "--agent", "demo", "--no-stack", "--db-host", host]
        try:
            played = objects(kk(*demo).stdout)[-1]["played"]
            again = objects(kk(*demo).stdout)[-1]["played"]
            mine, everything = views(base), views(base_all)
        finally:
            for s in (p, q):
                s.terminate()
        check("M5-T9 kk demo --distribution business loads the interview data; again changes nothing",
              played["said"] == 5 and played["claims"] >= 40 and again["said"] == 0 and again["claims"] == 0, f"{played} {again}")
        if True:
            check("M5-T9 the business stack's UI lists only its views (map, bpm)", mine == ["view-bpm", "view-map"], str(mine))
            check("M5-T9 a plain stack lists them all", everything and set(mine) < set(everything) and "view-devops" in everything,
                  str(everything))
        if True:
            time.sleep(1)
            if not os.environ.get("KK_KEEP_DB"):
                subprocess.run(["dropdb", "--if-exists", "--force", db], capture_output=True)
    print(f"M5-T9 distributions passed ({sum(checks)}/{len(checks)})" if all(checks) else "M5-T9 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

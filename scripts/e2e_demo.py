"""M4-G1: the process loop end to end, as a customer sees it; run last by scripts/smoke_kernel.sh.

`kk demo` loads order-to-cash (the event log, the documented model, the written procedure) and connects a
demo project. The owner signs in as the UI does (POST /ui/login) and, with that token over MCP as the UI
does, confirms the discrepancies waiting for review; the process learner proposes the frequent ones as
revisions; the owner approves one; conformance retracts that discrepancy. The demo agent's prompt hook
then answers a question about the process with claims cited by id and belief. `kk demo` again changes
nothing.

Without Docker (the smoke test): a fresh database on the smoke test's Postgres, with its own `kk serve`
(the process loop included) as kk_authenticator (DATABASE_URL, its database replaced). It resets the
owner's password, as `kk up` does for a new home: run it after the checks that use the owner's login.
With KK_DEMO_STACK=1 (CI, with Docker): `kk demo` starts the compose stack itself (KK_DB_PORT for
Postgres), and the stack is taken down at the end with its volumes (`down -v`): never run it where deploy/
holds a stack you keep.

Env: DATABASE_URL (kk_authenticator), KK_JWT_SECRET, KK_ARTIFACTS, PG* (a superuser); gitleaks on PATH.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_g1 import cited, harness, settle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STACK = os.environ.get("KK_DEMO_STACK") == "1"
QUESTION = "Which deviations from the order-to-cash process were confirmed, and in how many cases?"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def sh(*cmd, env=None) -> str:
    out = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"{' '.join(cmd[:4])} failed: {(out.stderr or out.stdout)[-1500:]}")
    return out.stdout


def post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def tool(base: str, token: str, name: str, **args):
    msg = post(base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": args}}, token)
    res = msg["result"]
    if res.get("isError"):
        raise RuntimeError(res["content"][0]["text"])
    sc = res.get("structuredContent")
    return sc.get("result", sc) if sc is not None else json.loads(res["content"][0]["text"])


def up(base: str) -> bool:
    try:
        urllib.request.urlopen(base + "/ui/views.json", timeout=5).close()
        return True
    except OSError:
        return False


def until(fn, timeout=180):
    deadline = time.time() + timeout
    while True:
        got = fn()
        if got or time.time() > deadline:
            return got
        time.sleep(2)


def kind(cs) -> str:
    return next((o["object"]["entity"].rsplit(":", 1)[-1] for o in cs["ops"]
                 if o["predicate"] == "http://purl.org/dc/terms/type"), "")


def text(cs) -> str:
    return next((o["object"]["value"]["@value"] for o in cs["ops"] if o["predicate"].endswith("bodyValue")), "")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "KK_HOME": str(Path(tmp) / "kk-home")}
        project = Path(tmp) / "kk-demo"
        serve, log = None, open(Path(tmp) / "serve.log", "w")
        try:
            if STACK:
                base = "http://127.0.0.1:8080"
                demo = ["kk", "demo", "--project", str(project), "--agent", "demo"]
            else:
                db = f"kk_demo_{int(time.time())}"
                sh("createdb", db)
                sh(sys.executable, "-m", "kk", "init", env={**env, "KK_ADMIN_URL": f"dbname={db}"})
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                base = f"http://127.0.0.1:{port}"
                env["KK_ADMIN_URL"] = f"dbname={db}"
                serve_url = urlunparse(urlparse(os.environ["DATABASE_URL"])._replace(path="/" + db))
                serve = subprocess.Popen([sys.executable, "-m", "kk", "serve", "--port", str(port), "--only",
                                          "mcp,validator,belief,recall,quotes,conformance,learner"],
                                         env={**env, "DATABASE_URL": serve_url, "KK_SWEEP_SECONDS": "3"},
                                         stdout=log, stderr=subprocess.STDOUT)
                if not until(lambda: serve.poll() is None and up(base), timeout=60):
                    raise SystemExit(f"kk serve did not start: {Path(log.name).read_text()[-1500:]}")
                host = f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"
                demo = ["kk", "demo", "--project", str(project), "--agent", "demo", "--no-stack", "--db-host", host]
            t0 = time.time()
            first = json.loads(sh(sys.executable, "-m", *demo, env=env))                    # 1. kk demo
            check("M4-G1 kk demo loads the log, the model and the procedure and connects the demo project",
                  first["loaded"].get("events") and first["loaded"].get("model") and first["loaded"].get("procedure")
                  and first.get("owner_sign_in"), json.dumps(first["loaded"]))

            signed = post(base + "/ui/login", first["owner_sign_in"])                          # 2. sign in
            token = signed["token"]
            check("M4-G1 the owner signs in with the printed name and password", signed["role"] == "person_owner")

            pending = lambda: [cs for cs in tool(base, token, "review_queue", limit=200)  # noqa: E731
                               if cs["ops"][0]["subject"].startswith("urn:kk:discrepancy:")]
            found = until(lambda: len(pending()) >= 6 and pending())                         # 3. review
            check("M4-G1 the deviations wait for review, each a sentence with its case count",
                  len(found) == 6 and all(re.search(r"\d", text(cs)) for cs in found),
                  ", ".join(sorted(kind(cs) for cs in found)))
            for cs in found:
                tool(base, token, "review", change_set_id=cs["id"], approve=True, note="confirmed in the demo")
            proposals = until(lambda: [cs for cs in tool(base, token, "review_queue", limit=200)
                                       if cs["ops"][0]["subject"].startswith("urn:kk:")
                                       and kind(cs) == "add-path"])
            swap = next((cs for cs in proposals or [] if "Send invoice to Ship goods" in text(cs)), None)
            check("M4-G1 confirmed, the frequent deviations come back as proposals to learn",
                  swap is not None, f"{len(proposals or [])} proposals")
            tool(base, token, "review", change_set_id=swap["id"], approve=True, note="learned in the demo")  # 4. learn
            swap_iri = "urn:kk:discrepancy:"
            left = until(lambda: not [c for c in tool(base, token, "query_claims",
                                                      predicate="http://purl.org/dc/terms/type",
                                                      object_entity="urn:kk:discrepancy-kind:swap", limit=50)
                                      if c["subject"].startswith(swap_iri) and c.get("status") != "deprecated"])
            check("M4-G1 approved, the path is documented and the next conformance pass retracts the swap", left)

            fire = harness(project, str(Path(tmp) / "state"))                                 # 5. ask the agent
            fire("SessionStart", {"session_id": "demo", "cwd": str(project), "source": "startup"})
            answer = settle(fire, project, lambda t: cited(t, "Ship goods"), timeout=120, question=QUESTION)
            refs = re.findall(r"\[claim ([0-9a-f-]+), belief ([0-9.]+|unscored)\]", answer)
            check("M4-G1 the agent's prompt recalls the confirmed deviations, cited by claim id with belief",
                  refs and any(b != "unscored" for _, b in refs) and "Ship goods" in answer,
                  f"{len(refs)} cited; {answer[answer.find('Ship goods') - 80:][:200]!r}")
            check("M4-G1 from the demo command to the learned process and a cited answer within ten minutes",
                  time.time() - t0 < 600, f"{time.time() - t0:.0f} s")

            again = json.loads(sh(sys.executable, "-m", *demo, env=env))
            check("M4-G1 kk demo again changes nothing", again["loaded"] == first["loaded"]
                  and not again.get("changed"), json.dumps(again["loaded"]))
        finally:
            if serve:
                serve.terminate()
    print(f"M4-G1 the process loop as a customer sees it passed ({sum(checks)}/{len(checks)})" if all(checks)
          else "M4-G1 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        if STACK:
            subprocess.run(["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "--env-file",
                            str(ROOT / "deploy/.env"), "down", "-v"], capture_output=True)

"""M3-G1: zero to a cited answer on your own repository, run by scripts/smoke_kernel.sh.

The steps a person takes, timed: clone a repository (this one), `kk up` it (--no-stack here: the smoke
test's kernel; with the stack, kk up starts it first and scans in the kernel image), and open the harness:
the session hook connects as the project's agent, and the first prompt about the project recalls claims
about its packages, cited by id with their belief. Ten minutes at most. Then the checkout moves on (a
commit changes a dependency): the session hook says the kernel's copy is stale; `kk up` again refreshes
it, the old requirement superseded.

Env: KK_ADMIN_URL (superuser), PGHOST/PGPORT, gitleaks on PATH; the knowledge-kernel package installed.
With KK_G1_STACK=1 (CI, with Docker): kk up starts the compose stack itself (KK_DB_PORT for Postgres)
and scans in the kernel image; the stack is taken down at the end.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "Which packages does this project require, and which version of psycopg?"
LIMIT = 600
STACK = os.environ.get("KK_G1_STACK") == "1"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def sh(*cmd, cwd=None, env=None) -> str:
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"{' '.join(cmd[:4])} failed: {(out.stderr or out.stdout)[-1500:]}")
    return out.stdout


def up(project: Path, env: dict) -> dict:
    if STACK:
        return json.loads(sh(sys.executable, "-m", "kk", "up", str(project), "--agent", "g1", env=env))
    db = f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"
    return json.loads(sh(sys.executable, "-m", "kk", "up", str(project), "--agent", "g1", "--no-stack",
                         "--db-host", db, env=env))


def harness(project: Path, state: str):
    """Fire the project's hooks as its settings name them, with nothing in the environment but PATH/HOME."""
    settings = json.loads((project / ".claude/settings.local.json").read_text())
    ours = {e: next(h["command"] for x in v for h in x["hooks"] if "kernel-mcp-hook" in h["command"])
            for e, v in settings["hooks"].items() if any("kernel-mcp-hook" in h["command"] for x in v for h in x["hooks"])}
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")} | {"KK_STATE_DIR": state}

    def fire(event, payload):
        out = subprocess.run(ours[event], shell=True, input=json.dumps(payload), capture_output=True, text=True,
                             env=env, cwd=project)
        return out.stdout + out.stderr
    return fire


def settle(fire, project: Path, want, timeout: float = 120, question: str = QUESTION) -> str:
    """The prompt hook's recall once `want(text)` holds (the validator and belief model are asynchronous)."""
    deadline = time.time() + timeout
    while True:
        got = fire("UserPromptSubmit", {"session_id": "g1", "prompt": question, "cwd": str(project)})
        if want(got) or time.time() > deadline:
            return got
        time.sleep(1)


def cited(text: str, obj: str) -> list[tuple[str, str]]:
    """(claim id, belief) of each recalled line whose object is `obj`."""
    return re.findall(rf"{re.escape(obj)}[^\[\n]*\[claim ([0-9a-f-]+), belief ([0-9.]+|unscored)\]", text)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.time()
        project = Path(tmp) / "kernel"
        sh("git", "clone", "-q", "--depth", "1", f"file://{ROOT}", str(project))                 # 1. clone
        env = {**os.environ, "KK_HOME": str(Path(tmp) / "kk-home")}
        first = up(project, env)                                                                  # 2. kk up
        enc = first.get("encoded", {})
        check("M3-G1 kk up encodes the project as the repository encoder",
              enc.get("repo") == "kernel" and enc.get("encoded", 0) > 50 and not enc.get("blocked"),
              json.dumps(enc)[:200])
        fire = harness(project, str(Path(tmp) / "state"))                                         # 3. the harness
        started = fire("SessionStart", {"session_id": "g1", "cwd": str(project), "source": "startup"})
        check("M3-G1 the session connects as the project's agent; the kernel has this commit",
              "urn:kk:agent:g1" in started and "stale" not in started and "not in the kernel" not in started,
              started[:160])
        scored = lambda t: (c := cited(t, "pkg:pypi/psycopg")) and all(b != "unscored" for _, b in c)
        answer = settle(fire, project, scored, timeout=LIMIT - (time.time() - t0))
        elapsed = time.time() - t0
        psycopg = cited(answer, "pkg:pypi/psycopg")
        check("M3-G1 the first prompt recalls the project's requirements, cited by claim id with belief",
              psycopg and all(b != "unscored" for _, b in psycopg) and len(re.findall(r"\[claim [0-9a-f-]+, belief", answer)) >= 3,
              f"{psycopg[:1]}")
        check(f"M3-G1 clone to a cited answer in at most {LIMIT // 60} minutes, three commands", elapsed < LIMIT,
              f"{elapsed:.0f} s")

        py = project / "pyproject.toml"
        py.write_text(re.sub(r'"psycopg\[binary\]==[^"]+"', '"psycopg[binary]==9.9.9"', py.read_text()))
        sh("git", "-c", "user.name=g1", "-c", "user.email=g1@example.org", "commit", "-qam", "bump psycopg", cwd=project)
        moved = fire("SessionStart", {"session_id": "g1b", "cwd": str(project), "source": "startup"})
        check("M3-G1 a moved checkout: the session hook says the kernel's copy is stale", "stale" in moved, moved[-200:])
        again = up(project, env)
        refreshed = fire("SessionStart", {"session_id": "g1c", "cwd": str(project), "source": "startup"})
        mine = lambda t: re.findall(r"pkg:pypi/knowledge-kernel \S+ pkg:pypi/psycopg \(version ([^)]+)\)", t)
        answer = settle(fire, project, lambda t: mine(t) == ["==9.9.9"], timeout=60,
                        question="Which version of psycopg does knowledge-kernel require?")
        check("M3-G1 kk up again refreshes it: no longer stale, the package's new requirement replaces the old",
              "stale" not in refreshed and mine(answer) == ["==9.9.9"],
              f"{mine(answer)} {json.dumps(again['encoded'].get('change_sets'))}")
    print(f"M3-G1 zero to a cited answer passed ({sum(checks)}/{len(checks)})" if all(checks)
          else "M3-G1 zero to a cited answer FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        if STACK:
            subprocess.run(["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "--env-file",
                            str(ROOT / "deploy/.env"), "down", "-v"], capture_output=True)

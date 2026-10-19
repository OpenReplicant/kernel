"""kk up PROJECT: connect a project folder to a kernel in one command (M3-T1, D-57).

1. The kernel: the compose stack (deploy/compose.yaml) is started, or left running; its secrets are
   generated once into deploy/.env. With --no-stack, the kernel at KK_ADMIN_URL is used as it is.
2. Logins: the owner (reviews, may redact) and the project's agent (default claude-code), each acting
   only as itself. Their connection strings are kept outside the project, mode 600, in KK_HOME
   (default ~/.config/knowledge-kernel): owner.env and agents/<name>.env. A login that still works is
   kept; one that does not gets a new password.
3. The project: the MCP entry in .mcp.json (it names the login file, never the secret, and the tool
   profile: memory by default, four tools; --profile operator for every tool, M6-T1), the
   knowledge-kernel skill in .claude/skills/, and the hooks in .claude/settings.local.json. Merged
   into what is there: other servers, hooks and settings are kept; a file is written only if it changes.
4. The project's contents (M3-G1, D-69): the repository encoder reads it as its own role (kk scan;
   the ingest filter first), in the kernel image with the stack (it carries gitleaks), here with
   --no-stack. Unchanged files change nothing; the session hook says when the checkout moved on.

Prints the UI address, the owner's sign-in (name and password, D-74) and token (when KK_JWT_SECRET is known) and what changed. Running it
again changes nothing.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import psycopg

from .cli import ROOT, add_identity, mint

HOOKS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "SessionEnd": "session-end"}
SKILL = ROOT / "modules/kernel-mcp/SKILL.md"


def home() -> Path:
    h = Path(os.environ.get("KK_HOME") or Path.home() / ".config/knowledge-kernel")
    (h / "agents").mkdir(parents=True, exist_ok=True)
    h.chmod(0o700)
    return h


def read_env(path: Path) -> dict:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and line[:1] != "#")


def write_secret(path: Path, values: dict) -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def shown(path: Path) -> str:
    """A path as the project's files name it: under the home folder, through ${HOME}."""
    try:
        return "${HOME}/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- the stack
def stack(env_file: Path) -> tuple[str, str]:
    """Start the compose stack (secrets generated once); return the admin URL and the JWT secret."""
    env = read_env(env_file)
    if not env.get("POSTGRES_PASSWORD"):
        example = read_env(ROOT / "deploy/.env.example")
        env = {**example, **{k: v for k, v in env.items() if v},
               "POSTGRES_PASSWORD": env.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(24),
               "KK_AUTHENTICATOR_PASSWORD": env.get("KK_AUTHENTICATOR_PASSWORD") or secrets.token_urlsafe(24),
               "KK_JWT_SECRET": env.get("KK_JWT_SECRET") or secrets.token_urlsafe(48),
               **({"KK_DB_PORT": os.environ["KK_DB_PORT"]} if os.environ.get("KK_DB_PORT") else {})}
        write_secret(env_file, env)
    # Built every time: Docker's layer cache makes an unchanged checkout a no-op, and an updated one is
    # never run on an image of older code.
    ca = os.environ.get("KK_BUILD_CA")    # a TLS-intercepting proxy's CA, as a build secret
    subprocess.run(["docker", "build", "-q", "-t", "knowledge-kernel:local",
                    *(["--secret", f"id=ca,src={ca}"] if ca else []), str(ROOT)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "--env-file", str(env_file),
                    "up", "-d", "--wait", "db", "kernel", "caddy"], check=True, stdout=subprocess.DEVNULL)
    port = os.environ.get("KK_DB_PORT") or env.get("KK_DB_PORT") or "5432"
    return f"postgresql://postgres:{env['POSTGRES_PASSWORD']}@127.0.0.1:{port}/kk", env["KK_JWT_SECRET"]


def ready(url: str, timeout: float = 120) -> None:
    """Wait until the kernel answers through Caddy (compose starts it before it listens)."""
    import time
    import urllib.request
    deadline = time.time() + timeout
    while True:
        try:
            urllib.request.urlopen(url, timeout=5).close()
            return
        except OSError:
            if time.time() > deadline:
                sys.exit(f"the kernel did not answer at {url}: docker compose -f deploy/compose.yaml logs kernel")
            time.sleep(1)


# ---------------------------------------------------------------- logins
def works(url: str | None) -> bool:
    if not url:              # never "" : libpq would fall back to the PG* variables
        return False
    try:
        with psycopg.connect(url, connect_timeout=5):
            return True
    except psycopg.Error:
        return False


def login(admin_url: str, db_host: str | None, path: Path, role: str, iri: str, kind: str, label: str,
          groups: list[str]) -> bool:
    """Keep a working login; otherwise (re)create it. True if it changed. db_host: Postgres as the
    harness reaches it (default: as the admin connection does)."""
    if works(read_env(path).get("KK_DATABASE_URL")):
        return False
    pw = secrets.token_urlsafe(24)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        out = add_identity(conn, role, iri, kind, label, groups, pw)
        url = f"postgresql://{role}:{quote(pw, safe='')}@{db_host}/{quote(conn.info.dbname)}" if db_host \
            else out["database_url"]
    write_secret(path, {"KK_DATABASE_URL": url})
    return True


# ---------------------------------------------------------------- the project's contents
def encode(folder: Path, admin_url: str, env_file: Path | None) -> dict:
    """Scan the project into the kernel as the repository encoder; the scan's summary, or its error."""
    from self_encoder.repo import commit
    args = ["--name", folder.name] + (["--commit", c] if (c := commit(folder)) else [])
    if env_file:   # the stack: the kernel image, the project read-only, artifacts in their volume
        cmd = ["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "--env-file", str(env_file), "run",
               "--rm", "--no-deps", "-v", f"{folder}:/repo:ro", "-v", "kk-artifacts:/artifacts",
               "-e", "KK_ARTIFACTS=/artifacts", "-e", "KK_ROLE=kkm_self_encoder",
               "kernel", "kk", "scan", "--repo", "/repo", *args]
        env = os.environ
    else:          # here, as the encoder's role through the admin login
        cmd = [sys.executable, "-m", "kk", "scan", "--repo", str(folder), *args]
        env = {**os.environ, "DATABASE_URL": admin_url, "KK_ROLE": "kkm_self_encoder"}
    out = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if out.returncode:
        return {"error": (out.stderr or out.stdout).strip()[-600:]}
    summary = json.loads(out.stdout.strip().splitlines()[-1])
    return {k: summary[k] for k in ("repo", "commit", "files", "encoded", "blocked", "change_sets") if k in summary}


# ---------------------------------------------------------------- the project
def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}


def command(name: str) -> str:
    """The installed kernel-mcp command next to this Python, else on PATH."""
    here = Path(sys.executable).parent / name
    return str(here) if here.exists() else shutil.which(name) or name


def mcp_entry(login_file: Path, profile: str = "memory") -> dict:
    return {"command": command("kernel-mcp"), "env": {"KK_LOGIN_FILE": shown(login_file), "KK_PROFILE": profile}}


def with_hooks(settings: dict, login_file: Path, profile: str = "memory") -> dict:
    """Add the kernel's hooks unless one of ours is already configured for that event: the three session
    hooks, and with the memory profile the pre-tool hook that brings up memories cued by a tool or a path
    (kernel-mcp-cue, D-91; it reads no database)."""
    hooks = settings.setdefault("hooks", {})
    if profile == "memory":
        entries = hooks.setdefault("PreToolUse", [])
        if not any("kernel-mcp-cue" in h.get("command", "") for e in entries for h in e.get("hooks", [])):
            entries.append({"hooks": [{"type": "command", "timeout": 5, "command": command("kernel-mcp-cue")}]})
    for event, arg in HOOKS.items():
        entries = hooks.setdefault(event, [])
        if any("kernel-mcp-hook" in h.get("command", "") for e in entries for h in e.get("hooks", [])):
            continue
        entries.append({"hooks": [{"type": "command", "timeout": 10,
                                   "command": f'KK_LOGIN_FILE="{shown(login_file)}" KK_PROFILE={profile} '
                                              f'{command("kernel-mcp-hook")} {arg}'}]})
    return settings


def project(folder: Path, login_file: Path, profile: str = "memory") -> list[str]:
    changed = []
    mcp = folder / ".mcp.json"
    cfg = load_json(mcp)
    cfg.setdefault("mcpServers", {})["kernel"] = mcp_entry(login_file, profile)
    if write_if_changed(mcp, json.dumps(cfg, indent=2) + "\n"):
        changed.append(".mcp.json")
    if write_if_changed(folder / ".claude/skills/knowledge-kernel/SKILL.md", SKILL.read_text()):
        changed.append(".claude/skills/knowledge-kernel/SKILL.md")
    local = folder / ".claude/settings.local.json"
    if write_if_changed(local, json.dumps(with_hooks(load_json(local), login_file, profile), indent=2) + "\n"):
        changed.append(".claude/settings.local.json")
    return changed


def connect(folder: Path, agent: str, no_stack: bool, db_host: str | None, no_scan: bool,
            profile: str = "memory") -> dict:
    """Steps 1–4 for one project folder; what kk up prints. Also out["admin_url"] (not printed)."""
    if not folder.is_dir():
        sys.exit(f"{folder} is not a folder")
    h = home()
    if no_stack:
        admin_url, jwt = os.environ.get("KK_ADMIN_URL", ""), os.environ.get("KK_JWT_SECRET", "")
    else:
        admin_url, jwt = stack(ROOT / "deploy/.env")
    agent_file = h / "agents" / f"{agent}.env"
    changed = [f for f, did in (
        (str(h / "owner.env"), login(admin_url, db_host, h / "owner.env", "person_owner", "urn:kk:agent:person:owner",
                                     "person", "Local owner", ["kk_reviewer", "kk_steward"])),
        (str(agent_file), login(admin_url, db_host, agent_file, f"agent_{agent.replace('-', '_')}",
                                f"urn:kk:agent:{agent}", "software", agent, ["kk_agent"]))) if did]
    changed += project(folder, agent_file, profile)
    out = {"project": str(folder), "agent": f"urn:kk:agent:{agent}", "changed": changed,
           "owner_login": str(h / "owner.env")}
    if not no_scan:
        out["encoded"] = encode(folder, admin_url, None if no_stack else ROOT / "deploy/.env")
    if not no_stack:
        out["ui"] = "http://127.0.0.1:8080/ui/"
        ready(out["ui"] + "views.json")
    if pw := urlparse(read_env(h / "owner.env").get("KK_DATABASE_URL", "")).password:
        out["owner_sign_in"] = {"name": "owner", "password": unquote(pw)}   # the UI's sign-in (D-74)
    if len(jwt) >= 32:
        out["owner_token"] = mint("person_owner", jwt, 30)
    out["admin_url"] = admin_url
    return out


def main(argv: list[str]) -> None:
    import argparse

    from .cli import name
    ap = argparse.ArgumentParser(prog="kk up", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("project", type=Path, nargs="?", default=Path("."))
    ap.add_argument("--agent", type=name, default="claude-code", help="the project's agent (default claude-code)")
    ap.add_argument("--no-stack", action="store_true", help="use the kernel at KK_ADMIN_URL; start nothing")
    ap.add_argument("--db-host", help="Postgres host:port as the harness reaches it (default: as KK_ADMIN_URL does)")
    ap.add_argument("--no-scan", action="store_true", help="connect the project without reading its contents")
    ap.add_argument("--profile", choices=("memory", "operator"), default="memory",
                    help="the agent's MCP tools: memory (four: remember, recall, explain, changes_since; default)"
                         " or operator (every tool)")
    a = ap.parse_args(argv)
    out = connect(a.project.resolve(), a.agent, a.no_stack, a.db_host, a.no_scan, a.profile)
    folder = Path(out["project"])
    print(json.dumps({k: v for k, v in out.items() if k != "admin_url"}, indent=2))
    print(f"\nReady. Open the project in your harness (Claude Code: `claude` in {folder}); approve the"
          " 'kernel' MCP server when asked." + (f" Review in the UI at {out['ui']}: sign in as owner (owner_sign_in)."
                                                 if "ui" in out else ""), file=sys.stderr)

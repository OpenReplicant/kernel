"""M3-T1: `kk up` connects a project folder to a kernel in one command, run by scripts/smoke_kernel.sh
(--no-stack: the smoke test's kernel; the compose stack is the same code after its start).

A fixture project that already has its own MCP server, hook and setting gets the kernel's MCP entry,
skill and hooks merged in; no secret is written into the project. A harness session started from those
files alone (the MCP server as .mcp.json names it, the hooks as the settings name them, with no login in
the environment) is the project's agent with the memory profile (M6-T1): it sees four tools, remembers a fact,
recalls it with belief and a handle, and explains the handle; the hooks recall it. Running kk up again changes nothing.

Env: KK_ADMIN_URL (superuser), the knowledge-kernel package installed (pip install -e .).
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

AGENT = "urn:kk:agent:up-test"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def up(project: Path, env: dict) -> dict:
    out = subprocess.run([sys.executable, "-m", "kk", "up", str(project), "--agent", "up-test", "--no-stack",
                          "--db-host", f"{os.environ.get('PGHOST') or 'localhost'}:{os.environ.get('PGPORT') or 5432}"],
                         capture_output=True, text=True, env=env)
    if out.returncode:
        raise SystemExit(f"kk up failed: {out.stderr[-1500:]}")
    return json.loads(out.stdout)


def bare() -> dict:
    """What a harness started from the project's files has: no login in the environment."""
    return {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}


async def tool(s, name, args=None):
    res = await s.call_tool(name, args or {})
    if res.is_error:
        return None, res.content[0].text
    sc = res.structured_content
    return (sc.get("result", sc) if sc else json.loads(res.content[0].text)), None


async def session(project: Path) -> str | None:
    entry = json.loads((project / ".mcp.json").read_text())["mcpServers"]["kernel"]
    env = {**bare(), **{k: os.path.expandvars(v) for k, v in entry.get("env", {}).items()}}
    params = StdioServerParameters(command=os.path.expandvars(entry["command"]), args=entry.get("args", []), env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        check("M6-T1 the project's agent sees the memory profile: four tools",
              names == {"remember", "recall", "explain", "changes_since"}, f"{sorted(names)}")
        text = f"Zanzibar ledger reconciliation runs nightly ({int(time.time() * 1000)})"
        res, err = await tool(s, "remember", {"kind": "fact", "text": text})
        check("M3-T1 the harness's MCP server, from .mcp.json alone, is the project's agent: it remembers",
              res and res["status"] in ("submitted", "valid", "applied"), err or res)
        line = ""
        for _ in range(60):
            brief, err = await tool(s, "recall", {"question": "When does Zanzibar ledger reconciliation run?"})
            line = next((x for x in (brief or "").splitlines() if text in x), "")
            if "belief" in line:
                break
            await asyncio.sleep(0.5)
        check("M3-T1 it recalls the memory as its own, with belief and a handle",
              "— you; belief" in line and "#" in line, err or line)
        handle = line.rsplit(" ", 1)[-1]
        why, err = await tool(s, "explain", {"claim_id": handle})
        check("M3-T1 the handle explains to the claim, attributed to the project's agent",
              why and why["claim"]["attributed_to"] == AGENT, err or "")
        return why["claim"]["id"] if why else None


def hooks(project: Path, state: str, claim: str | None) -> None:
    settings = json.loads((project / ".claude/settings.local.json").read_text())
    ours = {e: [h["command"] for x in v for h in x["hooks"] if "kernel-mcp-hook" in h["command"]]
            for e, v in settings["hooks"].items()}

    def fire(event, payload):
        out = subprocess.run(ours[event][0], shell=True, input=json.dumps(payload), capture_output=True, text=True,
                             env={**bare(), "KK_STATE_DIR": state})
        return out.stdout + out.stderr

    started = fire("SessionStart", {"session_id": "up-smoke", "cwd": str(project), "source": "startup"})
    recalled = fire("UserPromptSubmit", {"session_id": "up-smoke", "prompt": "How do we do Zanzibar ledger reconciliation?"})
    fire("SessionEnd", {"session_id": "up-smoke", "reason": "other"})
    check("M3-T1 the hooks, as the settings name them, connect as the project's agent and recall its claim",
          AGENT in started and claim and claim in recalled, started[:100])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        project, home = Path(tmp) / "project", Path(tmp) / "kk-home"
        (project / ".claude").mkdir(parents=True)
        (project / "README.md").write_text("# A project\n")
        (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other-server"}}}))
        mine = {"permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        (project / ".claude/settings.local.json").write_text(json.dumps(mine))
        env = {**os.environ, "KK_HOME": str(home)}

        first = up(project, env)
        mcp = json.loads((project / ".mcp.json").read_text())["mcpServers"]
        settings = json.loads((project / ".claude/settings.local.json").read_text())
        check("M3-T1 merged into the project: the MCP entry, the skill and the hooks; its own server, hook and setting kept",
              {"other", "kernel"} <= set(mcp) and (project / ".claude/skills/knowledge-kernel/SKILL.md").exists()
              and settings["permissions"] == mine["permissions"]
              and settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo mine"
              and all(any("kernel-mcp-hook" in h["command"] for x in settings["hooks"][e] for h in x["hooks"])
                      for e in ("SessionStart", "UserPromptSubmit", "SessionEnd")),
              f"changed {first['changed']}")
        login = (home / "agents/up-test.env").read_text().split("=", 1)[1].strip()
        secret = login.split(":")[2].split("@")[0]
        leaked = [str(p.relative_to(project)) for p in project.rglob("*") if p.is_file() and secret in p.read_text()]
        check("M3-T1 the agent's secret stays outside the project (mode 600)",
              not leaked and (home / "agents/up-test.env").stat().st_mode & 0o077 == 0, f"{leaked}")

        claim = asyncio.run(session(project))
        hooks(project, str(Path(tmp) / "state"), claim)
        again = up(project, env)
        check("M3-T1 running kk up again changes nothing", again["changed"] == [], f"{again['changed']}")
    print(f"M3-T1 kk up passed ({sum(checks)}/{len(checks)})" if all(checks) else "M3-T1 kk up FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

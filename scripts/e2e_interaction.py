"""M5-T3: the interaction encoder on a live kernel, run by scripts/smoke_kernel.sh while `kk serve` runs.

The encoder (as its own role) encodes an interview (chat JSON lines) and a Claude Code session: the
change sets are applied; the agent reads the messages and actions back; each quote occurs in the stored
transcript; the same file again changes nothing; a grown interview supersedes the claims it replaces;
the agent's recall (the memory profile) shows a message with the ones just before and after it (D-92).

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_interaction), AGENT_DATABASE_URL, KK_ARTIFACTS;
gitleaks on PATH (the ingest filter); the knowledge-kernel package installed.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "modules/encoder-interaction/tests/fixtures"
AS = "https://www.w3.org/ns/activitystreams#"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def encode(path: Path, *args) -> dict:
    out = subprocess.run([sys.executable, "-m", "interaction_encoder", str(path), *args], capture_output=True, text=True,
                         env={**os.environ, "KK_ROLE": "kkm_encoder_interaction"})
    if out.returncode:
        raise SystemExit(f"interaction_encoder failed: {out.stderr[-1500:] or out.stdout}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def settled(_, ids) -> list[str]:
    """The change sets' statuses once the validator has decided them (seen as the encoder: change sets
    are visible to the role that submitted them)."""
    db = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    db.execute("set role kkm_encoder_interaction")
    for _ in range(240):
        rows = [db.execute("select status from api.change_sets where id = %s", (i,)).fetchone()[0] for i in ids]
        if "submitted" not in rows:
            return rows
        time.sleep(0.25)
    return rows


async def recall(question: str) -> str:
    """The agent's recall briefing, through the kernel MCP server in the memory profile."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env.update(DATABASE_URL=os.environ["AGENT_DATABASE_URL"], KK_PROFILE="memory")
    params = StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("recall", {"question": question, "budget": 2000})
        return res.content[0].text if res.content else ""


def main() -> int:
    stamp = str(int(time.time()))
    work = Path(tempfile.mkdtemp())
    interview = work / "interview.jsonl"
    shutil.copy(FIX / "interview.jsonl", interview)
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    first = encode(interview, "--source", f"acme{stamp}")
    status = settled(agent, first["change_sets"])
    check("M5-T3 an interview is encoded as the encoder's role and applied",
          status == ["applied"] and first["messages"] == 4 and first["actions"] == 1, f"{status} {first['claims']} claims")
    m2 = f"urn:kk:message:acme{stamp}:acme-intake:2"
    row = agent.execute("""select object_value->>'@value', (select array_agg(object_entity) from api.claims
                                  where subject = %s and predicate = %s)
                           from api.claims where subject = %s and predicate = %s""",
                        (m2, AS + "attributedTo", m2, AS + "content")).fetchone()
    check("M5-T3 the agent reads a message back: its text and its author",
          row and "procurement lead" in row[0] and row[1] == [f"urn:kk:actor:acme{stamp}:dana-reyes"], row)
    store = Path(os.environ["KK_ARTIFACTS"]) / first["artifact"]
    text = store.read_text()
    quotes = [q for (q,) in agent.execute("""select distinct r->>'quote' from api.claims c
                                              cross join lateral jsonb_array_elements(api.explain(c.id)->'references') r
                                              where c.change_set = any(%s::uuid[]) and r->>'quote' is not null""",
                                          (first["change_sets"],)).fetchall()]
    check("M5-T3 every quote occurs in the stored transcript", quotes and all(q in text for q in quotes),
          f"{len(quotes)} quotes")
    again = encode(interview, "--source", f"acme{stamp}")
    check("M5-T3 the same interview again changes nothing", again["change_sets"] == first["change_sets"])
    with open(interview, "a") as f:
        f.write(json.dumps({"id": "6", "conversation": "acme-intake", "author": "Dana Reyes",
                            "time": "2026-10-18T09:02:00Z", "text": "Sam signs off within two days."}) + "\n")
    grown = encode(interview, "--source", f"acme{stamp}")
    settled(agent, grown["change_sets"])
    old = agent.execute("select status from api.claims where change_set = any(%s::uuid[]) and subject = %s and predicate = %s",
                        (first["change_sets"], m2, AS + "content")).fetchone()
    new = agent.execute("select count(*) from api.claims where subject = %s and status <> 'deprecated'",
                        (f"urn:kk:message:acme{stamp}:acme-intake:6",)).fetchone()[0]
    check("M5-T3 a grown interview supersedes the claims it replaces and adds the new message",
          old and old[0] == "deprecated" and grown["superseded"] > 0 and new > 0, (old, grown["superseded"], new))
    brief, lines = "", []
    for _ in range(40):   # recall's index catches up with the new claims
        brief = asyncio.run(recall("Anything over 10,000 needs approval first?"))
        lines = brief.splitlines()
        if any("needs Sam's approval" in x for x in lines):
            break
        time.sleep(0.5)
    hit = next((i for i, x in enumerate(lines) if "needs Sam's approval" in x), None)
    check("M5-T3 recall shows a message with the one before and after it, under its conversation (D-92)",
          hit is not None and any(x.startswith("- in acme-intake") for x in lines[:hit])
          and "What happens after you receive it?" in lines[hit - 1] and "Sam signs off" in lines[hit + 1]
          and lines[hit].startswith("  - 2026-10-18 Dana Reyes (person): "), brief[:900])
    session = encode(FIX / "session.jsonl", "--source", f"dev{stamp}", "--person", "ana")
    settled(agent, session["change_sets"])
    used = sorted(u for (u,) in agent.execute("""select object_entity from api.claims where predicate = %s
                                                 and subject like %s""",
                                              ("http://www.w3.org/ns/prov#used", f"urn:kk:action:dev{stamp}:%")).fetchall())
    check("M5-T3 a Claude Code session: its tool calls and the files and URLs they used",
          session["format"] == "claude-code" and used == ["file:src/client.py", "https://example.org/docs/retries"], used)
    print(f"M5-T3 interaction encoder passed ({sum(checks)}/{len(checks)})" if all(checks) else "M5-T3 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

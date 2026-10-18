"""M5-T3: the interaction encoder on a live kernel, run by scripts/smoke_kernel.sh while `kk serve` runs.

The encoder (as its own role) encodes an interview (chat JSON lines) and a Claude Code session: the
change sets are applied; the agent reads the messages and actions back; each quote occurs in the stored
transcript; the same file again changes nothing; a grown interview supersedes the claims it replaces.

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_interaction), AGENT_DATABASE_URL, KK_ARTIFACTS;
gitleaks on PATH (the ingest filter); the knowledge-kernel package installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg

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

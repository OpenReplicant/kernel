"""M6-T4: the memory extractor on a live kernel, run by scripts/smoke_kernel.sh while `kk serve` runs.

A stand-in model (a local OpenAI-compatible endpoint with fixed answers) lets the check run without a
model. A chat is encoded; the extractor (as its own role) reads each message once: a fact becomes a memory
quoting the message, the assistant's reply gives none, and a later correction supersedes the fact. The
quote verifier supports the new memory's quote, and the agent's recall shows it.

Env: DATABASE_URL (a login that may SET ROLE kkm_encoder_interaction and kkm_memory_extractor),
AGENT_DATABASE_URL, KK_ARTIFACTS; gitleaks on PATH; the knowledge-kernel package installed.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import psycopg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

AS = "https://www.w3.org/ns/activitystreams#"
DCT = "http://purl.org/dc/terms/"
checks = []
asked = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def answer(prompt: str) -> list[dict]:
    """The stand-in model: fixed memories for the fixture's messages, replacing the earlier one it lists."""
    message = prompt.split("\nMESSAGE (", 1)[1].split("\n", 1)[1].rsplit("\n\nJSON array:", 1)[0]
    earlier = re.findall(r"^(\d+)\. (.*)$", prompt.split("EARLIER MEMORIES:", 1)[1].split("\nPREVIOUS", 1)[0], re.M)
    asked.append(message)
    if "adopted a cat named Miso" in message:
        return [{"kind": "fact", "text": "The user adopted a cat named Miso.", "quote": "I just adopted a cat named Miso",
                 "about": "Miso"}]
    if "Mochi, not Miso" in message:
        n = next((int(i) for i, text in earlier if "Miso" in text), None)
        return [{"kind": "correction", "text": "The user's cat is named Mochi.", "quote": "her name is Mochi, not Miso",
                 "replaces": n}]
    return []


class Model(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"model": "stand-in", "choices": [{"message": {
            "content": json.dumps(answer(body["messages"][0]["content"]))}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_):
        pass


async def recall(question: str) -> str:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env.update(DATABASE_URL=os.environ["AGENT_DATABASE_URL"], KK_PROFILE="memory")
    params = StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("recall", {"question": question})
        return res.content[0].text if res.content else ""


def waiting(agent, sql, args, until, tries=120):
    row = None
    for _ in range(tries):
        row = agent.execute(sql, args).fetchall()
        if until(row):
            break
        time.sleep(0.5)
    return row


def main() -> int:
    stamp = str(int(time.time()))
    source = f"pets{stamp}"
    chat = Path(tempfile.mkdtemp()) / "chat.jsonl"
    lines = [{"id": "1", "conversation": "pets", "author": "Ana", "time": "2026-10-18T09:00:00Z",
              "text": "Hello! I just adopted a cat named Miso."},
             {"id": "2", "conversation": "pets", "author": "assistant", "kind": "agent", "time": "2026-10-18T09:00:10Z",
              "text": "Congratulations! Cats like a quiet corner at first."},
             {"id": "3", "conversation": "pets", "author": "Ana", "time": "2026-10-18T09:01:00Z",
              "text": "Sorry, I misspoke: her name is Mochi, not Miso."}]
    texts = [x["text"] for x in lines]
    chat.write_text("".join(json.dumps(x) + "\n" for x in lines))
    server = HTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = subprocess.run([sys.executable, "-m", "interaction_encoder", str(chat), "--source", source],
                         capture_output=True, text=True, env={**os.environ, "KK_ROLE": "kkm_encoder_interaction"})
    if out.returncode:
        raise SystemExit(f"interaction_encoder failed: {out.stderr[-1500:] or out.stdout}")
    worker = subprocess.Popen([sys.executable, "-m", "memory_extractor"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env={**os.environ, "KK_EXTRACT_URL": f"http://127.0.0.1:{server.server_port}",
                                              "KK_EXTRACT_KEY": "stand-in", "KK_SWEEP_SECONDS": "1"})
    agent = psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True)
    try:
        m1, m3 = (f"urn:kk:message:{source}:pets:{n}" for n in (1, 3))
        notes = """select p.object_entity, c.object_value->>'@value', c.status, c.id::text, c.evidence_type
                     from api.claims p join api.claims c on c.subject = p.subject and c.predicate = %s
                    where p.predicate = %s and p.object_entity = any(%s) order by p.object_entity"""
        rows = waiting(agent, notes, (AS + "content", DCT + "isPartOf", [m1, m3]),
                       lambda r: any(x[0] == m3 for x in r) and any(x[0] == m1 and x[2] == "deprecated" for x in r))
        fact = next((x for x in rows if x[0] == m1), None)
        fixed = next((x for x in rows if x[0] == m3 and x[2] != "deprecated"), None)
        check("M6-T4 a message's fact becomes a memory, extracted, quoting the message",
              fact and fact[1] == "The user adopted a cat named Miso." and fact[4] == "extracted", rows)
        check("M6-T4 a later correction supersedes the memory it corrects",
              fact and fact[2] == "deprecated" and fixed and fixed[1] == "The user's cat is named Mochi.", rows)
        check("M6-T4 each message is read once; the assistant's reply gives no memory",
              sorted(m for m in asked if m in texts) == sorted(texts) and not any(x[0].endswith(":2") for x in rows),
              f"{len(asked)} asked")
        verdict = waiting(agent, "select verdict from api.verifications where claim_id = %s::uuid",
                          (fixed[3] if fixed else "00000000-0000-4000-8000-000000000000",), lambda r: bool(r))
        check("M6-T4 the quote verifier supports the memory's quote", verdict == [("supported",)], verdict)
        brief = ""
        for _ in range(40):
            brief = asyncio.run(recall("What is my cat called?"))
            if "named Mochi" in brief:
                break
            time.sleep(0.5)
        line = next((x for x in brief.splitlines() if "named Mochi" in x), "")
        check("M6-T4 recall shows the memory, as said by the person who wrote the message",
              line.startswith('- correction: "The user\'s cat is named Mochi."') and "— Ana" in line
              and '- fact: "The user adopted a cat named Miso."' not in brief, brief[:600])
    finally:
        worker.terminate()
        log = worker.communicate(timeout=10)[0]
        server.shutdown()
    if not all(checks):
        print(log[-2000:])
    print(f"M6-T4 memory extractor passed ({sum(checks)}/{len(checks)})" if all(checks) else "M6-T4 FAILED")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

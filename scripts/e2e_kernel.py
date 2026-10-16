"""G4 end to end (kernel alone), run by scripts/smoke_kernel.sh: a fresh agent session reaches the kernel
through kernel-mcp over stdio, submits a claim, sees the validator apply it, finds and explains it;
the kernel refuses what that identity may not do; the shared HTTP mode refuses missing or forged tokens.

Env: DATABASE_URL (kk_authenticator, for the http server), AGENT_DATABASE_URL (agent_claude_code's
own login), OWNER_DATABASE_URL (person_owner's own login), PYTHONPATH including modules/kernel-mcp.
"""
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx
import jwt
import psycopg
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
ME = "urn:kk:agent:claude-code"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


def stdio(dsn):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env["DATABASE_URL"] = dsn
    return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env))


async def tool(s, name, args=None):
    res = await s.call_tool(name, args or {})
    text = res.content[0].text if res.content else ""
    if res.is_error:
        return None, text
    sc = res.structured_content
    return (sc.get("result", sc) if sc else json.loads(text)), None


def change_set(run, subject, ops_extra=None, **over):
    op = {"op": "add_claim", "subject": subject, "predicate": "rdfs:label",
          "object": {"value": {"@value": "Kernel smoke test entity", "@language": "en"}},
          "evidence_type": "testimony", "references": [{"source_uri": "file:scripts/e2e_kernel.py"}]}
    op.update(ops_extra or {})
    return {"idempotency_key": f"claude-code:{subject}", "attributed_to": ME, "generated_by": run,
            "target_scope": "urn:kk:scope:default", "ops": [op], **over}


async def settle(s, cs_id):
    for _ in range(60):
        row, _ = await tool(s, "get_change_set", {"change_set_id": cs_id})
        if row["status"] != "submitted":
            return row
        await asyncio.sleep(0.5)
    return row


async def agent_session():
    async with stdio(os.environ["AGENT_DATABASE_URL"]) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        check("tools listed over stdio", {"search", "get_entity", "query_claims", "explain", "submit_change_set",
                                          "start_run", "finish_run", "get_change_set"} <= names, f"{len(names)} tools")

        run, err = await tool(s, "start_run", {"info": {"purpose": "G4 kernel smoke"}})
        check("starts a run", run, err or run)
        subject = f"urn:kk:test:kernel:{int(time.time() * 1000)}"
        t0 = time.time()
        sub, err = await tool(s, "submit_change_set", {"change_set": change_set(run, subject)})
        check("submits under its own identity", sub and sub["status"] == "submitted", err or "")
        row = await settle(s, sub["id"])
        check("validated and applied without a runner", row["status"] == "applied",
              f"{row['status']} in {time.time() - t0:.1f}s")

        found, _ = await tool(s, "search", {"text": "smoke test entity"})
        mine = [f for f in found if f["subject"] == subject]
        check("search finds it by label", mine, f"{len(found)} matches")
        claim = mine[0]
        check("the claim is attributed to the caller and carries id and belief",
              claim["attributed_to"] == ME and "belief" in claim,
              f"claim {claim['id']} belief {claim['belief']} (no belief module installed)")
        why, _ = await tool(s, "explain", {"claim_id": claim["id"]})
        check("explain gives the source", why["references"][0]["source_uri"] == "file:scripts/e2e_kernel.py")
        card, _ = await tool(s, "get_entity", {"iri": subject})
        check("entity card", [c["id"] for c in card["claims"]] == [claim["id"]])

        bad = change_set(run, subject + ":bad", {"evidence_type": "extracted"})
        sub, _ = await tool(s, "submit_change_set", {"change_set": bad})
        row = await settle(s, sub["id"])
        check("an extracted claim without a quote is found invalid, with the reason",
              row["status"] == "invalid" and row["validation_report"], json.dumps(row["validation_report"])[:120])

        spoof = change_set(run, subject + ":spoof", attributed_to="urn:kk:agent:validator")
        _, err = await tool(s, "submit_change_set", {"change_set": spoof})
        check("cannot submit as another agent", err and "attributed" in err, (err or "")[:90])
        prop = change_set(run, subject + ":prop", {"subject": "http://example.org/smokeProperty", "predicate": "rdf:type",
                                                   "object": {"entity": "owl:DatatypeProperty"}})
        sub, _ = await tool(s, "submit_change_set", {"change_set": prop})
        row = await settle(s, sub["id"])
        check("typing a new property goes to the review lane", row["status"] == "awaiting_review", row["status"])
        reviewed = subprocess.run([sys.executable, str(ROOT / "scripts/review.py"), "approve", sub["id"], "--note", "smoke"],
                                  capture_output=True, text=True,
                                  env={**os.environ, "DATABASE_URL": os.environ["OWNER_DATABASE_URL"]})
        row, _ = await tool(s, "get_change_set", {"change_set_id": sub["id"]})
        check("a person approves it (scripts/review.py) and it is applied", row["status"] == "applied",
              (reviewed.stdout + reviewed.stderr).strip()[:100])

        done, _ = await tool(s, "finish_run", {"run_id": run, "status": "succeeded"})
        check("finishes its run", done == "succeeded")

    async with stdio(os.environ["DATABASE_URL"]) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        _, err = await tool(s, "query_claims")
        check("stdio refuses a login that can switch roles", err and "own login" in err, (err or "")[:70])

    with psycopg.connect(os.environ["AGENT_DATABASE_URL"]) as conn:
        try:
            conn.execute("set role person_owner")
            became = True
        except psycopg.Error:
            became = False
        check("the agent's login cannot become the owner", not became)


async def http_session():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    secret = secrets.token_hex(32)
    proc = subprocess.Popen([sys.executable, "-m", "kernel_mcp", "--http"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env={**os.environ, "PORT": str(port), "KK_JWT_SECRET": secret})
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(50):
            try:
                httpx.get(url, timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        exp = int(time.time()) + 300
        tokens = {"none": None, "forged": jwt.encode({"role": "agent_claude_code", "exp": exp}, "wrong" * 8, "HS256"),
                  "valid": jwt.encode({"role": "agent_claude_code", "exp": exp}, secret, "HS256")}
        results = {}
        for name, token in tokens.items():
            client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=30)
            async with client, streamable_http_client(url, http_client=client) as (r, w, *_), ClientSession(r, w) as s:
                await s.initialize()
                results[name] = await tool(s, "query_claims", {"predicate": "rdfs:label", "limit": 5})
        check("http: no token refused", results["none"][1] and "bearer" in results["none"][1])
        check("http: forged token refused", results["forged"][1] and "invalid token" in results["forged"][1])
        check("http: valid token reads", results["valid"][1] is None and results["valid"][0],
              f"{len(results['valid'][0] or [])} claims")
    finally:
        proc.terminate()
        proc.wait(10)


async def main():
    await agent_session()
    await http_session()
    print("G4 kernel smoke", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
    return 0 if all(checks) else 1


sys.exit(asyncio.run(main()))

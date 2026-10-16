"""T8 end to end, run inside the kernel-mcp container (it has the MCP SDK): an agent reads, explains
and submits through MCP under its own identity, and the kernel refuses what that identity may not do.

Env: KK_MCP_URL (default http://localhost:8000/mcp), AGENT_JWT (agent_claude_code).
"""
import asyncio
import json
import os
import sys
import time

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("KK_MCP_URL", "http://localhost:8000/mcp")
ME = "urn:kk:agent:claude-code"
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok  " if ok else "FAIL"), name, detail)


async def session(token):
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)
    return http, streamable_http_client(URL, http_client=http)


async def tool(s, name, args=None):
    res = await s.call_tool(name, args or {})
    text = res.content[0].text if res.content else ""
    if res.is_error:
        return None, text
    sc = res.structured_content
    return (sc.get("result", sc) if sc else json.loads(text)), None


async def main():
    http, transport = await session(None)
    async with http, transport as (r, w, *_), ClientSession(r, w) as s:
        await s.initialize()
        _, err = await tool(s, "list_modules")
        check("no token is refused", err is not None and "bearer" in err, (err or "")[:80])

    http, transport = await session(os.environ["AGENT_JWT"])
    async with http, transport as (r, w, *_), ClientSession(r, w) as s:
        init = await s.initialize()
        names = sorted(t.name for t in (await s.list_tools()).tools)
        check("tools listed", {"search", "get_entity", "query_claims", "explain", "submit_change_set", "start_run",
                               "finish_run", "list_slots", "list_modules"} <= set(names),
              f"protocol {init.protocol_version}, {len(names)} tools")

        modules, _ = await tool(s, "list_modules")
        check("reads the self-model", len(modules) == 12 and all(m["type_claim"]["belief"] is not None for m in modules),
              f"{len(modules)} modules, each with a belief")
        slots, _ = await tool(s, "list_slots")
        check("reads the slot catalog", len(slots) == 42, f"{len(slots)} slots")

        card, _ = await tool(s, "get_entity", {"iri": "urn:kk:module:validator-shacl"})
        fills = [c for c in card["claims"] if c["predicate"].endswith("#fills")]
        check("entity card cites claim ids and belief",
              fills and fills[0]["object_entity"] == "urn:kk:slot:shape-validator" and fills[0]["belief"] is not None,
              f"claim {fills[0]['id'] if fills else None} belief {fills[0]['belief'] if fills else None}")

        why, _ = await tool(s, "explain", {"claim_id": fills[0]["id"]})
        ref = why["references"][0]
        check("explain gives the source and span", ref["artifact"] and ref["span"]["start_line"] >= 1,
              f"artifact {ref['artifact'][:12]}… lines {ref['span']}")

        found, _ = await tool(s, "search", {"text": "validator"})
        check("search finds entities", any(f["subject"] == "urn:kk:module:validator-shacl" for f in found),
              f"{len(found)} matches")

        run, _ = await tool(s, "start_run", {"info": {"purpose": "T8 end to end"}})
        subject = f"urn:kk:test:mcp:{int(time.time() * 1000)}"
        cs = {"idempotency_key": f"claude-code:{subject}", "attributed_to": ME, "generated_by": run,
              "target_scope": "urn:kk:scope:default",
              "ops": [{"op": "add_claim", "subject": subject, "predicate": "rdfs:comment",
                       "object": {"value": {"@value": "submitted through MCP"}}, "evidence_type": "testimony",
                       "references": [{"source_uri": "file:scripts/e2e_mcp.py"}]}]}
        submitted, err = await tool(s, "submit_change_set", {"change_set": cs})
        check("submits under its own identity", submitted and submitted["status"] == "submitted", err or "")
        status = None
        for _ in range(60):
            row, _ = await tool(s, "get_change_set", {"change_set_id": submitted["id"]})
            status = row["status"]
            if status != "submitted":
                break
            await asyncio.sleep(1)
        check("validated and applied by the platform", status == "applied", status)
        mine, _ = await tool(s, "query_claims", {"subject": subject})
        check("the claim is attributed to the caller", mine and mine[0]["attributed_to"] == ME,
              mine[0]["attributed_to"] if mine else "")
        done, _ = await tool(s, "finish_run", {"run_id": run, "status": "succeeded", "cost": {"tools": 12}})
        check("finishes its run", done == "succeeded")

        spoof = dict(cs, idempotency_key=cs["idempotency_key"] + ":spoof", attributed_to="urn:kk:agent:self-encoder")
        run2, _ = await tool(s, "start_run")
        spoof["generated_by"] = run2
        _, err = await tool(s, "submit_change_set", {"change_set": spoof})
        check("cannot submit as another agent", err is not None and ("KK010" in err or "attributed" in err),
              (err or "")[:90])

    print("T8 end-to-end", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
    return 0 if all(checks) else 1


sys.exit(asyncio.run(main()))

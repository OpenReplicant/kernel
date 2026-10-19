"""G4 end to end (kernel alone), run by scripts/smoke_kernel.sh: a fresh agent session reaches the kernel
through kernel-mcp over stdio, submits a claim, sees the validator apply it, finds and explains it;
the kernel refuses what that identity may not do; the shared HTTP mode refuses missing or forged tokens.

Env: DATABASE_URL (kk_authenticator), AGENT_DATABASE_URL (agent_claude_code's own login),
OWNER_DATABASE_URL (person_owner's own login), KK_MCP_URL and KK_JWT_SECRET (the running `kk serve`), KK_ADMIN_URL
(a superuser: a private scope for M6-T11);
the knowledge-kernel package installed (pip install -e .).
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
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


def stdio(dsn, profile=None, scope=None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env["DATABASE_URL"] = dsn
    if profile:
        env["KK_PROFILE"] = profile
    if scope:
        env["KK_MEMORY_SCOPE"] = scope
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


def hook(name, payload, state):
    """Run one harness hook as Claude Code would: hook JSON on stdin, the agent's own login."""
    out = subprocess.run([sys.executable, "-m", "kernel_mcp.hooks", name], input=json.dumps(payload),
                         capture_output=True, text=True,
                         env={**os.environ, "KK_DATABASE_URL": os.environ["AGENT_DATABASE_URL"],
                              "DATABASE_URL": "", "KK_STATE_DIR": state})
    ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout.strip() else ""
    return out.returncode, ctx, out.stderr


def hooks(claim):
    with tempfile.TemporaryDirectory() as state:
        session = {"session_id": "smoke-session", "cwd": str(ROOT), "source": "startup"}
        rc, ctx, err = hook("session-start", session, state)
        run = re.search(r"kernel run is ([0-9a-f-]{36})", ctx)
        check("hook session-start: the agent learns its identity and its session run",
              rc == 0 and ME in ctx and run, err or ctx[:80])
        rc, ctx, err = hook("prompt", {**session, "prompt": "What do we know about the smoke test entity?"}, state)
        check("hook prompt: recalls the claim with id and belief",
              rc == 0 and claim["id"] in ctx and "belief 0.50" in ctx, err or ctx.splitlines()[1][:100])
        rc, ctx, err = hook("session-end", {**session, "reason": "other"}, state)
        with psycopg.connect(os.environ["AGENT_DATABASE_URL"], autocommit=True) as conn:
            try:
                conn.execute("select api.finish_run(%s::uuid, 'succeeded')", (run.group(1),))
                finished = False
            except psycopg.Error:
                finished = True  # no longer among the agent's running runs
        check("hook session-end: the session's run is finished", rc == 0 and finished and not os.listdir(state),
              err)


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

        claim = None
        for _ in range(40):
            found, _ = await tool(s, "search", {"text": "smoke test entity"})
            mine = [f for f in found if f["subject"] == subject]
            if mine and mine[0]["belief"] is not None:
                claim = mine[0]
                break
            await asyncio.sleep(0.25)
        check("search finds it by label, scored by the belief model",
              claim and claim["belief"] == 0.5, f"{time.time() - t0:.1f}s after submit")
        check("the claim is attributed to the caller and carries id and belief", claim["attributed_to"] == ME,
              f"claim {claim['id']} belief {claim['belief']}")
        why, _ = await tool(s, "explain", {"claim_id": claim["id"]})
        check("explain gives the source", why["references"][0]["source_uri"] == "file:scripts/e2e_kernel.py")
        card, _ = await tool(s, "get_entity", {"iri": subject})
        check("entity card", [c["id"] for c in card["claims"]] == [claim["id"]])
        hooks(claim)

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
        reviewed = subprocess.run([sys.executable, "-m", "kk", "review", "approve", sub["id"], "--note", "smoke"],
                                  capture_output=True, text=True,
                                  env={**os.environ, "KK_DATABASE_URL": os.environ["OWNER_DATABASE_URL"]})
        row, _ = await tool(s, "get_change_set", {"change_set_id": sub["id"]})
        check("a person approves it (kk review) and it is applied", row["status"] == "applied",
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


async def recalled(s, question, text, want="belief"):
    """The briefing line holding `text`, once it carries `want` (the claim is applied and scored)."""
    line, err = "", None
    for _ in range(60):
        brief, err = await tool(s, "recall", {"question": question})
        line = next((x for x in (brief or "").splitlines() if text in x), "")
        if want in line:
            break
        await asyncio.sleep(0.25)
    return line, err or (None if line else (brief or "")[:600])


async def memory_session():
    """M6-T1: the memory profile, four tools; remember, recall, explain a handle, correct, changes since."""
    t0 = await asyncio.to_thread(lambda: psycopg.connect(os.environ["AGENT_DATABASE_URL"]).execute(
        "select now()").fetchone()[0])
    stamp = int(time.time() * 1000)
    text = f"We reconcile the Quokka ledger every night at two ({stamp})"
    fixed = f"We reconcile the Quokka ledger every night at three ({stamp})"
    async with stdio(os.environ["AGENT_DATABASE_URL"], "memory") as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        check("M6-T1 the memory profile lists four tools", names == {"remember", "recall", "explain", "changes_since"},
              f"{sorted(names)}")
        _, err = await tool(s, "query_claims")
        check("M6-T1 a tool outside the profile is refused", err and "not in your profile" in err, (err or "")[:80])
        res, err = await tool(s, "remember", {"kind": "decision", "text": text, "about": "urn:kk:test:quokka-ledger"})
        check("M6-T1 remember: one sentence, no change set", res and res["memory"].startswith("urn:kk:memory:"), err or res)
        again, _ = await tool(s, "remember", {"kind": "decision", "text": text, "about": "urn:kk:test:quokka-ledger"})
        check("M6-T1 remembering the same again is the same change set", again and again["change_set"] == res["change_set"])
        line, err = await recalled(s, "When do we reconcile the Quokka ledger?", text)
        check("M6-T1 recall: a briefing line with its kind, author, belief and handle",
              line.startswith(f'- decision: "{text}"') and "— you; belief 0.50 #" in line, err or line)
        handle = line.rsplit(" ", 1)[-1]
        why, err = await tool(s, "explain", {"claim_id": handle})
        check("M6-T1 explain(handle) gives the source: the agent's own words, quoted",
              why and why["references"][0]["quote"] == text and why["claim"]["evidence_type"] == "testimony",
              err or "")
        named, err = await tool(s, "remember", {"kind": "fact", "text": f"The Quokka ledger lives in Hobart ({stamp})",
                                                 "about": f"Quokka ledger {stamp}"})
        line, _ = await recalled(s, "Where does the Quokka ledger live?", "lives in Hobart")
        res, _ = await tool(s, "remember", {"kind": "fact", "text": f"The Quokka ledger has two owners ({stamp})",
                                            "about": f"quokka LEDGER {stamp}"})
        both, _ = await recalled(s, "Who owns the Quokka ledger?", "two owners")
        check("M6-T1 about: a name becomes a labelled topic, and the same name later finds it",
              f"(about Quokka ledger {stamp})" in line and f"(about Quokka ledger {stamp})" in both, err or both)
        res, err = await tool(s, "remember", {"kind": "correction", "text": fixed, "replaces": handle})
        line, err = await recalled(s, "When do we reconcile the Quokka ledger?", fixed)
        old, _ = await recalled(s, "When do we reconcile the Quokka ledger?", text, want="never")
        why, _ = await tool(s, "explain", {"claim_id": handle})
        check("M6-T1 a correction supersedes the memory: recalled instead of it, the old one kept and marked",
              line.startswith(f'- correction: "{fixed}"') and not old
              and why["status"][-1]["status"] == "deprecated" and why["status"][-1]["reason"] == "superseded",
              line)
        res = await s.read_resource("kernel://recall/When%20do%20we%20reconcile%20the%20Quokka%20ledger%3F")
        check("M6-T1 the briefing is also an MCP resource", fixed in res.contents[0].text, res.contents[0].text[:120])
    async with stdio(os.environ["OWNER_DATABASE_URL"], "memory") as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        news, err = await tool(s, "changes_since", {"since": t0.isoformat()})
        check("M6-T1 changes_since: another agent's memory, its superseded one, and the next cursor",
              news and f'correction: "{fixed}"' in news and "[superseded by #" in news and "next cursor:" in news,
              err or (news or "")[:200])


PRIVATE = "urn:kk:scope:e2e-memory-private"


async def conclusion_session():
    """M6-T11: a conclusion is recorded with its premises, as private as the most private of them."""
    with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as admin:
        admin.execute("insert into kernel.scope(iri, label) values (%s, 'memory smoke: private') on conflict do nothing",
                      (PRIVATE,))
        # The services that must: the validator and the index read it; the belief model and truth
        # maintenance record their beliefs and verdicts there.
        for role, write in (("person_owner", True), ("kkm_validator", False), ("kkm_belief", True),
                            ("kkm_recall_index", False), ("kkm_truth_maintenance", True)):
            admin.execute("""insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)
                             on conflict (scope, db_role) do update set can_read = true, can_write = excluded.can_write""",
                          (PRIVATE, role, write))
            # kernel.readers_subset compares grant rows, not role membership: for a claim in PRIVATE to be
            # derived from one in the default scope, each PRIVATE reader needs its own row there too.
            admin.execute("""insert into kernel.scope_grant(scope, db_role, can_read, can_write)
                             values ('urn:kk:scope:default', %s, true, false) on conflict (scope, db_role) do nothing""",
                          (role,))
    stamp = int(time.time() * 1000)
    q = "What do we know about the Wombat payroll?"
    owner = os.environ["OWNER_DATABASE_URL"]
    async with stdio(owner, "memory", PRIVATE) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await tool(s, "remember", {"kind": "fact", "text": f"The Wombat payroll runs on the 25th ({stamp})"})
        secret, _ = await recalled(s, q, "runs on the 25th")
    async with stdio(owner, "memory") as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await tool(s, "remember", {"kind": "fact", "text": f"The Wombat payroll pays forty people ({stamp})"})
        public, _ = await recalled(s, q, "forty people")
        _, err = await tool(s, "remember", {"kind": "conclusion", "text": "The Wombat payroll is small"})
        check("M6-T11 a conclusion without premises is refused", err and "because" in err, (err or "")[:80])
        both = [secret.rsplit(" ", 1)[-1], public.rsplit(" ", 1)[-1]]
        res, err = await tool(s, "remember", {"kind": "conclusion", "because": both,
                                              "text": f"The Wombat payroll pays forty people on the 25th ({stamp})"})
        line, err2 = await recalled(s, q, "forty people on the 25th")
        why, _ = await tool(s, "explain", {"claim_id": line.rsplit(" ", 1)[-1]}) if line else (None, None)
        check("M6-T11 a conclusion: inferred from the premises it cites, marked in the briefing",
              line.startswith("- conclusion:") and "(from 2 premises)" in line and why
              and why["claim"]["evidence_type"] == "inferred"
              and sorted(why["derived_from"]) == sorted(why2["claim"]["id"] for why2 in
                                                       [(await tool(s, "explain", {"claim_id": h}))[0] for h in both]),
              err or err2 or line)
        check("M6-T11 it is as private as its most private premise", why and why["claim"]["scope"] == PRIVATE,
              why and why["claim"]["scope"])
        await tool(s, "remember", {"kind": "correction", "replaces": both[1],
                                   "text": f"The Wombat payroll pays fifty people ({stamp})"})
        flagged, err = await recalled(s, q, "forty people on the 25th", want="a premise was withdrawn")
        belief = float(flagged.split("belief ")[1][:4]) if "belief " in flagged else 1
        why, _ = await tool(s, "explain", {"claim_id": flagged.rsplit(" ", 1)[-1]}) if flagged else (None, None)
        tms = [v for v in (why or {}).get("verifications", []) if v.get("method") == "urn:kk:module:truth-maintenance"]
        check("M6-T12 a premise superseded: the conclusion is flagged, its belief capped, and kept",
              "a premise was withdrawn" in flagged and belief <= 0.25 and tms and tms[-1]["verdict"] == "inconclusive"
              and why["claim"]["status"] != "deprecated", err or flagged)
    async with stdio(os.environ["AGENT_DATABASE_URL"], "memory") as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        brief, _ = await tool(s, "recall", {"question": q})
        check("M6-T11 an agent without that clearance sees the public premise (now corrected), not the conclusion",
              "fifty people (" in (brief or "") and "on the 25th" not in (brief or ""), (brief or "")[:200])


async def http_session():
    url, secret = os.environ["KK_MCP_URL"], os.environ["KK_JWT_SECRET"]
    for _ in range(50):
        try:
            httpx.get(url, timeout=1)
            break
        except httpx.TransportError:
            time.sleep(0.2)
    exp = int(time.time()) + 300
    tokens = {"none": None, "forged": jwt.encode({"role": "agent_claude_code", "exp": exp}, "wrong" * 8, "HS256"),
              "valid": subprocess.run([sys.executable, "-m", "kk", "token", "agent_claude_code", "--days", "0.01"],
                                      capture_output=True, text=True, check=True).stdout.strip()}
    results = {}
    for name, token in tokens.items():
        client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=30)
        async with client, streamable_http_client(url, http_client=client) as (r, w, *_), ClientSession(r, w) as s:
            await s.initialize()
            results[name] = await tool(s, "query_claims", {"predicate": "rdfs:label", "limit": 5})
    check("http: no token refused", results["none"][1] and "bearer" in results["none"][1])
    check("http: forged token refused", results["forged"][1] and "invalid token" in results["forged"][1])
    check("http: valid token (kk token) reads", results["valid"][1] is None and results["valid"][0],
          f"{len(results['valid'][0] or [])} claims")
    token = subprocess.run([sys.executable, "-m", "kk", "token", "agent_claude_code", "--days", "0.01", "--profile",
                            "memory"], capture_output=True, text=True, check=True).stdout.strip()
    client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    async with client, streamable_http_client(url, http_client=client) as (r, w, *_), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        _, err = await tool(s, "query_claims")
    check("M6-T1 http: a token's profile (kk token --profile memory) chooses the tools",
          names == {"remember", "recall", "explain", "changes_since"} and err and "not in your profile" in err,
          f"{sorted(names)}")


async def main():
    await agent_session()
    await memory_session()
    await conclusion_session()
    await http_session()
    print("G4 kernel smoke", "passed" if all(checks) else "FAILED", f"({sum(checks)}/{len(checks)})")
    return 0 if all(checks) else 1


sys.exit(asyncio.run(main()))

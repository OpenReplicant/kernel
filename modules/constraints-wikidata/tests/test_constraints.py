#!/usr/bin/env python3
"""Wikidata's property constraints on a throwaway kernel (D-108).

Makes a kernel (`kk init`, `kk serve --only validator`), has an encoder's role submit constraints (claims on
properties, as encoder-substrate carries them) and the claims they constrain, and runs the constraints module's
pass. Then, as the read model's own role (RLS applies) and through the kernel's MCP server in the substrate
profile as a harness agent's own login, for each kind (single-value, format, one-of, value-type):

  - the claims that break a constraint get the verdict inconclusive, with the constraint, kind and reason; the
    ones that do not get nothing; no claim is changed (a report, never a block)
  - running again submits nothing and records no run
  - fixed (a claim withdrawn, a constraint withdrawn, a value ranked above) -> supported, gone from the view
  - broken again -> reported again
  - substrate.violations and the `violations` tool say the same, with claim handles and belief
  - a private scope the module is not cleared for is neither seen nor reported, and the agent's tool shows it
    only to an agent that is cleared

No Substrate checkout needed. Needs Postgres 16+ (PGHOST/PGUSER/PGDATABASE as for psql; a superuser), this
repository installed (`pip install -e .`, with the MCP SDK) and, for the tool, pg_trgm (view-substrate's search).
Skips (exit 0) when one is missing.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable


def skip(why: str):
    print(f"skip: {why}")
    sys.exit(0)


try:
    import psycopg
    from psycopg.rows import dict_row
    from kk.lib import connect, run, submit
    from constraints_wikidata.__main__ import METHOD, change_sets, one_pass
    from mcp import ClientSession  # noqa: F401
    from mcp.client.stdio import StdioServerParameters, stdio_client
    import kernel_mcp  # noqa: F401
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")
try:
    admin = psycopg.connect(dbname=os.environ.get("PGDATABASE", "postgres"), autocommit=True)
    if not admin.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:
        skip("needs a superuser")
except psycopg.Error as e:
    skip(f"no Postgres: {str(e).strip()[:100]}")

DB = f"cw_kernel_{os.getpid()}"
PW = secrets.token_hex(12)
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")
WDT, WD, SUB, ITEM = ("http://www.wikidata.org/prop/direct/", "http://www.wikidata.org/entity/",
                      "https://w3id.org/knowledge-kernel/substrate#", "urn:kk:substrate:")
SINGLE, FORMAT, ONEOF, VTYPE = (WD + q for q in ("Q19474404", "Q21502404", "Q21510859", "Q21510865"))
URL, METRIC, VERDICT, PART = WDT + "P973", SUB + "metric", SUB + "verdict", WDT + "P527"
A, B, C, GOOD, BAD, KLASS, SUPER, OTHER = (ITEM + q for q in ("Q1", "Q2", "Q3", "Q10", "Q11", "Q20", "Q21", "Q22"))
DEFAULT, PRIVATE = "urn:kk:scope:default", "urn:kk:scope:cw-private"
AGENT = "constraints-test"
checks, fails, procs = 0, [], []


def check(name: str, ok: bool, detail: str = ""):
    global checks
    checks += 1
    if not ok:
        fails.append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def url(user: str, password: str = PW) -> str:
    if HOST.startswith("/"):
        return f"postgresql://{user}:{password}@/{DB}?host={HOST}&port={PORT}"
    return f"postgresql://{user}:{password}@{HOST}:{PORT}/{DB}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cleanup():
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    admin.execute(f'drop database if exists "{DB}" with (force)')
    admin.execute(f'drop role if exists "agent_{AGENT.replace("-", "_")}"')


def claim(subject: str, predicate: str, obj, qualifiers: dict | None = None, n: int = 0) -> dict:
    """What encoder-substrate makes of a store statement: an entity or a plain string, qualifiers by IRI."""
    op = {"op": "add_claim", "subject": subject, "predicate": predicate,
          "object": {"entity": obj} if isinstance(obj, str) and obj.startswith(("urn:", "http")) else {"value": {"@value": obj}},
          "evidence_type": "testimony",
          "references": [{"source_uri": f"urn:kk:substrate:reference:{secrets.token_hex(4)}?by=person:test"}]}
    if qualifiers:
        op["qualifiers"] = qualifiers
    return op


def constraint(prop: str, kind: str, **qualifiers) -> dict:
    return claim(prop, WDT + "P2302", kind, qualifiers or None)


async def tool(agent_url: str, name: str, args: dict) -> str:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env.update(DATABASE_URL=agent_url, KK_PROFILE="substrate")
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(name, args)
            return res.content[0].text if res.content else ""


def main():
    admin.execute(f'create database "{DB}"')
    kenv = {**os.environ, "KK_ADMIN_URL": f"dbname={DB}", "KK_AUTHENTICATOR_PASSWORD": PW,
            "KK_JWT_SECRET": secrets.token_hex(24), "DATABASE_URL": url("kk_authenticator")}
    subprocess.run([PY, "-m", "kk", "init"], check=True, env=kenv, cwd=ROOT, capture_output=True)
    procs.append(subprocess.Popen([PY, "-m", "kk", "serve", "--only", "validator", "--port", str(free_port())],
                                  env=kenv, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    made = json.loads(subprocess.run([PY, "-m", "kk", "agent", "add", AGENT], env=kenv, cwd=ROOT, capture_output=True,
                                     text=True, check=True).stdout.strip().splitlines()[-1])
    agent_url = url(made["role"], made["password"])
    kadmin = psycopg.connect(dbname=DB, autocommit=True, row_factory=dict_row)
    rd = psycopg.connect(url("kk_authenticator"), autocommit=True, row_factory=dict_row)
    rd.execute("set role kkm_view_substrate")
    mod = connect("kkm_constraints_wikidata", url=url("kk_authenticator"))

    def wait_applied():
        for _ in range(120):
            n = kadmin.execute("select count(*) as n from kernel.change_set where status not in "
                               "('applied', 'invalid', 'rejected', 'conflict')").fetchone()["n"]
            if n == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("change sets did not apply: is the validator running?")

    def say(ops: list[dict], scope: str = DEFAULT) -> list[str]:
        with connect("kkm_encoder_substrate", url=url("kk_authenticator")) as c, c.cursor() as cur:
            with run(cur, "urn:kk:module:encoder-substrate") as (rid, _):
                res = submit(cur, {"idempotency_key": f"t:{secrets.token_hex(6)}", "attributed_to": "urn:kk:agent:encoder-substrate",
                                   "target_scope": scope, "ops": ops}, rid)
        wait_applied()
        st = kadmin.execute("select status, validation_report from kernel.change_set where id = %s", (res["id"],)).fetchone()
        check(f"submitted {len(ops)} op(s) in {scope.rsplit(':', 1)[1]}: applied", st["status"] == "applied", str(st))
        return [str(kadmin.execute(            # the claims, in the order of the ops that made them
            "select id from kernel.claim where change_set = %s and subject = %s and predicate = %s "
            "and (object_entity = %s or object_value ->> '@value' = %s)",
            (res["id"], o["subject"], o["predicate"], o["object"].get("entity"), (o["object"].get("value") or {}).get("@value"))
        ).fetchone()["id"]) for o in ops if o["op"] == "add_claim"]

    def retract(claim_id: str, status: str = "deprecated", scope: str = DEFAULT):
        op = {"op": "set_status", "claim": claim_id, "status": status}
        if status == "deprecated":
            op["reason"] = "retracted"
        say([op], scope)

    def sweep() -> dict:
        one_pass(mod)
        wait_applied()
        return verdicts()

    def verdicts() -> dict:
        """The module's latest verdict on each claim: label of the claim -> (verdict, [(kind, message)])."""
        latest = {}
        for r in kadmin.execute("select v.claim_id::text as id, v.verdict, v.details from kernel.verification v "
                                "where v.method = %s order by v.recorded_at", (METHOD,)).fetchall():
            latest[r["id"]] = (r["verdict"], [(x["kind"], x["message"]) for x in r["details"]["violations"]])
        return latest

    def pending() -> list[dict]:
        with mod.cursor(row_factory=dict_row) as cur:
            return change_sets(cur)

    def view() -> set[tuple]:
        return {(str(r["statement_id"]), r["kind"]) for r in rd.execute("select * from substrate.violations").fetchall()}

    def runs() -> int:
        return kadmin.execute("select count(*) as n from kernel.run where module = %s", (METHOD,)).fetchone()["n"]

    # ---- the constraints, and claims that keep them or break them ---------------------------------------
    c_single, c_format, c_oneof, c_vtype = say([
        constraint(URL, SINGLE), constraint(METRIC, FORMAT, **{WDT + "P1793": "p[0-9]+"}),
        constraint(VERDICT, ONEOF, **{WDT + "P2305": [SUB + "better", SUB + "worse"]}),
        constraint(PART, VTYPE, **{WDT + "P2308": KLASS})])[:4]
    u1, u2, u3 = say([claim(A, URL, "https://a.example/1"), claim(A, URL, "https://a.example/2"), claim(B, URL, "https://b.example/1")])
    m_ok, m_bad = say([claim(A, METRIC, "p99"), claim(A, METRIC, "latency")])
    v_ok, v_bad = say([claim(A, VERDICT, SUB + "better"), claim(B, VERDICT, SUB + "same")])
    say([claim(GOOD, WDT + "P31", KLASS), claim(BAD, WDT + "P31", OTHER), claim(KLASS, WDT + "P279", SUPER)])
    p_direct, p_none, p_bad = say([claim(C, PART, GOOD), claim(C, PART, ITEM + "Q12"), claim(C, PART, BAD)])
    claims_before = kadmin.execute("select count(*) as n from kernel.claim").fetchone()["n"]

    plan = pending()
    check("a pass would submit one change set, in the default scope, of add_verification ops only",
          len(plan) == 1 and plan[0]["target_scope"] == DEFAULT and {o["op"] for o in plan[0]["ops"]} == {"add_verification"})
    v = sweep()
    got = {k: x[0] for k, x in v.items()}
    check("single-value: both values of a subject with two are reported, the lone one is not",
          got.get(u1) == got.get(u2) == "inconclusive" and u3 not in got, str(v))
    check("format: the string off its pattern is reported, the one that matches is not",
          got.get(m_bad) == "inconclusive" and m_ok not in got)
    check("one-of: a value outside the allowed ones is reported, an allowed one is not",
          got.get(v_bad) == "inconclusive" and v_ok not in got)
    check("value-type: an instance of another class and an item with no class are reported; an instance of a subclass is not",
          got.get(p_bad) == "inconclusive" and got.get(p_none) == "inconclusive" and p_direct not in got, str(v))
    check("nothing else was reported", set(got) == {u1, u2, m_bad, v_bad, p_bad, p_none}, str(sorted(got)))
    check("the reasons name the kind, the value and the constraint's parameters",
          v[u1][1] == [("single-value", "Q1 has 2 usable values for P973; it takes one")]
          and v[m_bad][1] == [("format", '"latency" does not match the format p[0-9]+')]
          and v[v_bad][1] == [("one-of", "same is not one of the values verdict allows")]
          and v[p_bad][1] == [("value-type", "Q11 is not an instance of a class P527 allows")], str(v))
    det = kadmin.execute("select details from kernel.verification where claim_id = %s and method = %s", (m_bad, METHOD)).fetchone()["details"]
    check("the details carry the constraint's claim id and the property",
          det["violations"][0]["constraint"] == c_format and det["violations"][0]["property"] == METRIC
          and det["check"] == "property constraints", str(det))
    check("a report, never a block: no claim was added or changed, the reported claims are still normal",
          kadmin.execute("select count(*) as n from kernel.claim").fetchone()["n"] == claims_before
          and kadmin.execute("select count(*) as n from api.claims where id = any(%s::uuid[]) and status = 'normal'",
                             ([m_bad, v_bad, p_bad, u1],)).fetchone()["n"] == 4)
    check("the verdicts are the module's: its method and its own agent, and only add_verification",
          kadmin.execute("select count(*) as n from kernel.verification v join kernel.run r on r.id = v.run where v.method = %s "
                         "and r.agent <> 'urn:kk:agent:constraints-wikidata'", (METHOD,)).fetchone()["n"] == 0)
    check("inconclusive, not refuted: the report does not cap belief by the verdict it gives",
          kadmin.execute("select count(*) as n from kernel.verification where method = %s and verdict = 'refuted'", (METHOD,)).fetchone()["n"] == 0)

    # ---- idempotent -------------------------------------------------------------------------------------
    before, n_runs = kadmin.execute("select count(*) as n from kernel.verification").fetchone()["n"], runs()
    check("running again plans nothing", pending() == [])
    sweep()
    check("running again submits nothing and records no run",
          kadmin.execute("select count(*) as n from kernel.verification").fetchone()["n"] == before and runs() == n_runs)

    # ---- the view and the tool --------------------------------------------------------------------------
    check("substrate.violations has a row per claim and constraint broken, in Substrate's columns",
          view() == {(u1, "single-value"), (u2, "single-value"), (m_bad, "format"), (v_bad, "one-of"), (p_bad, "value-type"),
                     (p_none, "value-type")}, str(view()))
    row = rd.execute("select * from substrate.violations where statement_id = %s", (m_bad,)).fetchone()
    check("substrate.violations: statement_id, subject, property, constraint_id, kind, message",
          list(row) == ["statement_id", "subject", "property", "constraint_id", "kind", "message"]
          and (row["subject"], row["property"], str(row["constraint_id"]), row["kind"]) == (A, METRIC, c_format, "format"))
    allv = asyncio.run(tool(agent_url, "violations", {}))
    lines = allv.splitlines()
    check("the violations tool lists them all, one line each, in Substrate's layout with the claim handle and belief",
          len(lines) == 6 and all(" (" in x and "): " in x and f"[statement " in x and " belief " in x and "#" in x for x in lines), allv)
    check("a line reads: subject property (kind): message [statement id, #handle belief]",
          any(x.startswith('Q1 metric (format): "latency" does not match the format p[0-9]+ [statement ' + m_bad + ", #" + m_bad[:8] + " belief ")
              for x in lines), allv)
    one = asyncio.run(tool(agent_url, "violations", {"id": "Q3"}))
    check("for one entity: only its own", len(one.splitlines()) == 2 and all(x.startswith("Q3 ") for x in one.splitlines()), one)
    check("an entity with none says none", asyncio.run(tool(agent_url, "violations", {"id": "Q2"})).splitlines()[0].startswith("Q2 verdict (one-of)")
          and asyncio.run(tool(agent_url, "violations", {"id": "Q10"})) == "none")

    # ---- fixed: cleared ---------------------------------------------------------------------------------
    retract(u2)
    v = sweep()
    check("single-value fixed (one of two withdrawn): the other is cleared, supported", v[u1] == ("supported", []), str(v[u1]))
    check("and it is gone from the view; the withdrawn one is not usable, so not shown",
          not {s for s, k in view() if s in (u1, u2)})
    retract(v_bad)
    retract(c_vtype)
    v = sweep()
    check("a claim withdrawn clears it, and a constraint withdrawn clears what it reported",
          v[v_bad][0] == "inconclusive" and v[p_bad] == ("supported", []) and v[p_none] == ("supported", []), str(v))
    check("the view follows", view() == {(m_bad, "format")}, str(view()))
    check("running again after the fixes submits nothing", pending() == [])

    # ---- broken again: reported again -------------------------------------------------------------------
    u4, = say([claim(A, URL, "https://a.example/4")])
    v = sweep()
    check("broken again: reported again, both values, in a new verdict",
          v[u1][0] == v[u4][0] == "inconclusive" and (u1, "single-value") in view() and (u4, "single-value") in view(), str(v.get(u1)))
    check("the message follows the count", v[u4][1][0][1] == "Q1 has 2 usable values for P973; it takes one")

    # ---- Wikidata's rank rule: a preferred value is the only usable one ---------------------------------
    retract(u4, "preferred")
    v = sweep()
    check("a preferred value makes the others unusable: no violation, and the reported one is cleared",
          v[u1] == ("supported", []) and v[u4][0] == "supported" and not {s for s, k in view() if k == "single-value"}, str(v))

    # ---- a constraint with parameters changed: the message follows --------------------------------------
    retract(c_format)
    c_format2, = say([constraint(METRIC, FORMAT, **{WDT + "P1793": "p[0-9]+|latency"})])
    v = sweep()
    check("a replaced constraint is checked afresh: the value that matches the new pattern is cleared",
          v[m_bad] == ("supported", []), str(v[m_bad]))
    c_format3, = say([constraint(METRIC, FORMAT, **{WDT + "P1793": "["})])
    sweep()
    check("a pattern that is not a regular expression checks nothing and breaks nothing",
          (m_ok not in verdicts() or verdicts()[m_ok][0] == "supported") and verdicts()[m_bad][0] == "supported")

    # ---- row-level security -----------------------------------------------------------------------------
    kadmin.execute("insert into kernel.scope(iri, label) values (%s, 'constraints test: private')", (PRIVATE,))
    for role, w in (("kkm_encoder_substrate", True), ("kkm_validator", False)):
        kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (PRIVATE, role, w))
    priv, = say([claim(C, METRIC, "secret-latency")], PRIVATE)
    sweep()
    check("rls: a private claim the module is not cleared for is not seen, so not reported", priv not in verdicts())
    check("rls: nor is it in the view, nor in the tool", priv not in {s for s, k in view()}
          and "secret" not in asyncio.run(tool(agent_url, "violations", {})))
    kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, 'kkm_constraints_wikidata', true, true)",
                   (PRIVATE,))
    sweep()
    check("rls: cleared for it, the module reports it, in that scope", verdicts().get(priv, ("",))[0] == "inconclusive"
          and kadmin.execute("select scope from kernel.claim where id = %s", (priv,)).fetchone()["scope"] == PRIVATE)
    check("rls: an agent that is not cleared still does not see it in the view or the tool",
          "secret" not in asyncio.run(tool(agent_url, "violations", {})))
    kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, false)", (PRIVATE, made["role"]))
    seen = asyncio.run(tool(agent_url, "violations", {}))
    check("rls: an agent cleared for the scope sees it", '"secret-latency"' in seen and "(format)" in seen, seen)
    kadmin.execute("delete from kernel.scope_grant where scope = %s and db_role = 'kkm_constraints_wikidata'", (PRIVATE,))
    retract(priv, scope=PRIVATE)
    check("rls: with the module's grant taken away it plans nothing for that scope",
          not any(cs["target_scope"] == PRIVATE for cs in pending()))

    check("the module made no tables of its own", kadmin.execute(
        "select count(*) as n from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where c.relowner = (select oid from pg_roles where rolname = 'kkm_constraints_wikidata')").fetchone()["n"] == 0)


try:
    main()
finally:
    cleanup()
if fails:
    sys.exit(f"FAIL: {len(fails)} of {checks}: {fails}")
print(f"D-108 property constraints on a kernel: checks passed ({checks}/{checks})")

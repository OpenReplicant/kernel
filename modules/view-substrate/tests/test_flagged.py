#!/usr/bin/env python3
"""What the span watcher flags shows in Substrate's read model and tools (D-107).

Makes a throwaway kernel (`kk init`, `kk serve --only validator`), has an encoder's role submit a small map
(a whole with two parts, each statement citing a line of a file, as encoder-substrate writes them), and runs the
watcher's pass over a temporary tree. Then, as the read model's own role (RLS applies) and through the kernel's
MCP server in the substrate profile as a harness agent's own login:

  - nothing changed: substrate.flag is empty, substrate.map has no flagged edge, page and locate show no flag
  - a line moved: the edge for the statement citing it is flagged in map, page and read say so, locate gives the
    reason and where the text went (moved_to), the other statement is not flagged
  - the line edited: flagged as changed, with found_at when the text is elsewhere
  - the file put back: the flag clears everywhere
  - a file deleted: flagged as gone

No Substrate checkout needed. Needs Postgres 16+ (PGHOST/PGUSER/PGDATABASE as for psql; a superuser) and this
repository installed (`pip install -e .`, with the MCP SDK). Skips (exit 0) when one is missing.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
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
    from watcher_spans.__main__ import Pass
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

DB = f"vf_kernel_{os.getpid()}"
PW = secrets.token_hex(12)
HOST, PORT = os.environ.get("PGHOST", "localhost"), os.environ.get("PGPORT", "5432")
ITEM, WDT = "urn:kk:substrate:", "http://www.wikidata.org/prop/direct/"
WHOLE, PART_A, PART_B, ROLE = (ITEM + q for q in ("Q901", "Q902", "Q903", "Q904"))
AGENT = "flagged-test"
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


def has_part(part: str, n: int, source: str, locator: str, quote: str) -> dict:
    return {"op": "add_claim", "subject": WHOLE, "predicate": WDT + "P527", "object": {"entity": part},
            "qualifiers": {WDT + "P3831": ROLE}, "evidence_type": "deterministic",
            "references": [{"source_uri": f"urn:kk:substrate:reference:r{n}?by=parser:test", "quote": quote,
                            "span": {"type": "FragmentSelector", "value": locator, "source": source}}]}


async def tools(agent_url: str, calls: list[tuple[str, dict]]) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env.update(DATABASE_URL=agent_url, KK_PROFILE="substrate")
    out = []
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name, args in calls:
                res = await s.call_tool(name, args)
                out.append(res.content[0].text if res.content else "")
    return out


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

    def wait_applied():
        for _ in range(120):
            n = kadmin.execute("select count(*) as n from kernel.change_set where status not in "
                               "('applied', 'invalid', 'rejected', 'conflict')").fetchone()["n"]
            if n == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("change sets did not apply: is the validator running?")

    watcher = connect("kkm_watcher_spans", url=url("kk_authenticator"))

    def sweep(roots):
        Pass(roots)(watcher)
        wait_applied()

    def edges():
        return {r["target"]: r["flagged"] for r in rd.execute(
            "select target, flagged from substrate.map(%s, 3) where kind = 'has-part'", (WHOLE,)).fetchall()}

    def located(target: str) -> list[dict]:
        return json.loads(asyncio.run(tools(agent_url, [("locate", {"target": target})]))[0])

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        parts, stable = root / "parts.txt", root / "stable.txt"
        parts.write_text("alpha\nthe chain fits 1/2 x 3/32\nomega\n")
        stable.write_text("steady line\n")
        with connect("kkm_encoder_substrate", url=url("kk_authenticator")) as c, c.cursor() as cur:
            with run(cur, "urn:kk:module:encoder-substrate") as (rid, _):
                submit(cur, {"idempotency_key": f"t:{secrets.token_hex(6)}", "attributed_to": "urn:kk:agent:encoder-substrate",
                             "target_scope": "urn:kk:scope:default",
                             "ops": [has_part(PART_A, 1, "file:parts.txt", "L2", "the chain fits 1/2 x 3/32"),
                                     has_part(PART_B, 2, "file:stable.txt", "L1", "steady line")]}, rid)
        wait_applied()
        claim_a = kadmin.execute("select id::text from kernel.claim where object_entity = %s", (PART_A,)).fetchone()["id"]

        sweep([root])
        check("unchanged: substrate.flag is empty", rd.execute("select count(*) as n from substrate.flag").fetchone()["n"] == 0)
        check("unchanged: no edge of the map is flagged", edges() == {PART_A: False, PART_B: False}, str(edges()))
        check("unchanged: the page has no flag", "flagged" not in rd.execute("select substrate.page(%s)", (WHOLE,)).fetchone()["page"])
        loc = located(WHOLE)
        check("unchanged: locate says flagged null, the span not changed",
              len(loc) == 2 and all(x["flagged"] is None and x["span"]["changed_since"] is False for x in loc), str(loc)[:200])

        parts.write_text("new first line\nalpha\nthe chain fits 1/2 x 3/32\nomega\n")
        sweep([root])
        check("a line moved: only the edge citing it is flagged", edges() == {PART_A: True, PART_B: False}, str(edges()))
        f = rd.execute("select * from substrate.flag").fetchall()
        check("a line moved: substrate.flag names the claim, the reason and the watcher's details",
              len(f) == 1 and str(f[0]["statement_id"]) == claim_a and f[0]["reason"] == "its span moved"
              and f[0]["details"]["changed"][0]["moved_to"] == "L3", str(f))
        page = rd.execute("select substrate.page(%s)", (WHOLE,)).fetchone()["page"]
        check("a line moved: the page marks that statement flagged and not the other",
              page.count("**flagged: its span moved**") == 1, page)
        loc = {x["claim"]: x for x in located(WHOLE)}
        a, b = loc[claim_a], next(x for k, x in loc.items() if k != claim_a)
        check("a line moved: locate reports the reason and where the text went",
              a["flagged"] == "its span moved" and a["span"]["changed_since"] is True and a["span"]["why"] == "moved"
              and a["span"]["moved_to"] == "L3", str(a))
        check("a line moved: locate on the other statement is not flagged", b["flagged"] is None, str(b))
        check("a line moved: locate by the claim's handle says the same",
              located("#" + claim_a[:8])[0]["flagged"] == "its span moved")
        read, = asyncio.run(tools(agent_url, [("read", {"id": "Q901"})]))
        check("a line moved: read marks the flagged claim, on the page and in its claims list",
              read.count("**flagged: its span moved**") == 2, read)

        # the watcher keeps a flagged claim's first verdict until it is restored: put it back, then edit
        parts.write_text("alpha\nthe chain fits 1/2 x 3/32\nomega\n")
        sweep([root])
        parts.write_text("alpha\nthe chain fits 1/8 inch\nomega\n")
        sweep([root])
        check("a line edited: flagged as changed", edges()[PART_A] is True and rd.execute(
            "select reason from substrate.flag").fetchone()["reason"] == "its span changed")
        check("a line edited: locate says changed", located("#" + claim_a[:8])[0]["span"]["why"] == "changed")

        parts.write_text("alpha\nthe chain fits 1/2 x 3/32\nomega\n")
        sweep([root])
        check("restored: the flag clears in flag, map, page and locate",
              rd.execute("select count(*) as n from substrate.flag").fetchone()["n"] == 0 and edges() == {PART_A: False, PART_B: False}
              and "flagged" not in rd.execute("select substrate.page(%s)", (WHOLE,)).fetchone()["page"]
              and all(x["flagged"] is None and x["span"]["changed_since"] is False for x in located(WHOLE)))

        stable.unlink()
        sweep([root])
        check("a file deleted: the edge is flagged, the reason says gone",
              edges() == {PART_A: False, PART_B: True} and rd.execute("select reason from substrate.flag").fetchone()["reason"] == "its span is gone")

        check("the read model reads only through the api views (security invoker)", kadmin.execute(
            "select bool_and(coalesce(c.reloptions::text, '') like '%%security_invoker=true%%') as ok from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'substrate' and c.relkind = 'v'").fetchone()["ok"])
        check("the read model added no tables", kadmin.execute(
            "select count(*) as n from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'substrate' and c.relkind in ('r', 'p')").fetchone()["n"] == 0)


try:
    main()
finally:
    cleanup()
if fails:
    sys.exit(f"FAIL: {len(fails)} of {checks}: {fails}")
print(f"D-107 flagged in the read model and tools: checks passed ({checks}/{checks})")

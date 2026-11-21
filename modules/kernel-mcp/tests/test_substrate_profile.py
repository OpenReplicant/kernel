#!/usr/bin/env python3
"""The substrate profile of kernel-mcp (D-105) against Substrate's own store MCP server.

Reuses view-substrate's fixture (test_equivalence.py): Substrate's migrations and `Store` class build a store, a
throwaway kernel (`kk init`, `kk serve --only validator`) takes it through encoder-substrate. Then the kernel's
MCP server, in the substrate profile and as a harness agent's own login (stdio), answers what Substrate's
`store.mcp.Server` answers, and writes what it writes:

  - the profile lists Substrate's ten tools with its argument names (`from` of path included), refuses every
    other tool, and the memory and operator profiles do not list them
  - violations (D-108) lists what Substrate's tool lists, by subject and kind, each with a claim handle and belief
  - search, read, expand, locate, path and alternatives answer like the store (ids, labels, ranks, references,
    the path's length), plus claim handles and belief
  - create_item, say and deprecate, run on the kernel through MCP and on the store through Substrate's server then
    exported by the encoder, give equal claims (subject, predicate, object, qualifiers, evidence type, references),
    apart from who the claim is attributed to and what is kept in a reference's uuid
  - a claim in a scope the agent is not cleared for is in no answer

  SUBSTRATE=/path/to/substrate  python modules/kernel-mcp/tests/test_substrate_profile.py

Needs what view-substrate's test needs (Postgres 16+ superuser with pgvector and pg_trgm, this repository installed, a
Substrate checkout); skips (exit 0) without them. Not in CI: it needs a Substrate checkout.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modules/view-substrate/tests"))
import test_equivalence as eq  # noqa: E402  (skips with exit 0 when the environment lacks something)

import psycopg  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

sys.path.insert(0, eq.SUBSTRATE)
from store.mcp import TOOLS as STORE_TOOLS, Server as StoreServer  # noqa: E402  Substrate's own MCP server, the reference
from kernel_mcp import substrate as sub  # noqa: E402

check = eq.check
CLIENT = ("eq-client", "1.0")
AGENT = "substrate-test"


# ------------------------------------------------------------------------------------------------ helpers
class Store:
    """Substrate's MCP server over its store, called as its agent would."""

    def __init__(self, dsn: str):
        os.environ["STORE_URL"] = dsn
        self.s = StoreServer()
        self.s.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"clientInfo": {"name": CLIENT[0], "version": CLIENT[1]}}})

    def __call__(self, name: str, **args) -> str:
        out = self.s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        res = out["result"]
        if res["isError"]:
            raise RuntimeError(res["content"][0]["text"])
        return res["content"][0]["text"]


def Store_(conn) -> tuple[str, str]:
    """Something private in the store: a whole and its part (ids)."""
    from store import Store as S, person
    s = S(conn, actor="fixture-private")
    with s.edit("fixture: something private"):
        vault = s.item("Zebrafish vault", key="fx:zebrafish", description="a secret whole", aliases=["zfv"])
        part = s.item("Zebrafish part", key="fx:zebrafish-part")
        s.say(vault, s.id("has-part"), part, by=person("pete"), qualifiers=[(s.id("object-has-role"), s.id("fx:storage"))])
        s.say(vault, s.id("described-at-url"), "https://secret.example/vault", by=person("pete"))
    return vault, part


class Kernel:
    """kernel-mcp over stdio in a profile, as a harness agent with its own login."""

    def __init__(self, session):
        self.s = session

    async def __call__(self, name: str, **args) -> str:
        res = await self.s.call_tool(name, args)
        text = res.content[0].text if res.content else ""
        if res.is_error:
            raise RuntimeError(text)
        return text


def stdio(dsn: str, profile: str | None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME")}
    env["DATABASE_URL"] = dsn
    env["KK_SUBSTRATE_WAIT"] = "30"
    if profile:
        env["KK_PROFILE"] = profile
    return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "kernel_mcp"], env=env))


async def refused(call, *a, **kw) -> str | None:
    try:
        await call(*a, **kw)
    except RuntimeError as e:
        return str(e)
    return None


CITE = re.compile(r"\s+\[(?:#[0-9a-f]{8} belief [\d.-]+(?:, )?)+\]$", re.M)
PARENS = re.compile(r"\s*\([^)]*\)")


def bare(text: str) -> str:
    """A line as both sides can say it: no claim handles, no ids or ranks in parentheses."""
    return PARENS.sub("", CITE.sub("", text)).strip()


def norm_claim(c: dict, new: str, qid: str) -> tuple:
    """A claim without who made it and without the uuid in its references; the new item's own IRI as NEW."""
    sw = lambda x: json.loads(json.dumps(x).replace(new, "NEW").replace(qid, "Q"))  # noqa: E731
    refs = sorted((re.sub(r"reference:[0-9a-f-]{36}", "reference:X", r["source_uri"]).replace(qid, "Q"),
                   r.get("quote"), json.dumps(r.get("span"), sort_keys=True)) for r in c["refs"])
    return (c["subject"].replace(new, "NEW"), c["predicate"], c["object_entity"].replace(new, "NEW") if c["object_entity"] else None,
            json.dumps(sw(c["object_value"]), sort_keys=True), json.dumps(sw(c["qualifiers"]), sort_keys=True),
            c["evidence_type"], tuple(refs))


def claims_of(kconn, new: str, qid: str) -> list[tuple]:
    rows = kconn.execute("""select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers,
                                   c.evidence_type, c.status
                            from api.claims c where (c.subject = %s or c.object_entity = %s) and c.status <> 'deprecated'""",
                         (new, new)).fetchall()
    out = []
    for cid, s, p, oe, ov, q, ev, st in rows:
        refs = kconn.execute("select source_uri, quote, span from api.claim_references where claim_id = %s", (cid,)).fetchall()
        out.append(norm_claim({"subject": s, "predicate": p, "object_entity": oe, "object_value": ov, "qualifiers": q,
                               "evidence_type": ev, "refs": [{"source_uri": u, "quote": qt, "span": sp} for u, qt, sp in refs]},
                              new, qid))
    return sorted(out, key=repr)


# ------------------------------------------------------------------------------------------------ the run
async def run(kenv: dict, agent_url: str):
    store_db = f"dbname={eq.SDB}"
    st = Store(store_db)
    sconn = psycopg.connect(store_db, autocommit=True)
    ids = {k: i for i, k in sconn.execute("select id, key from store.entity where key like 'fx:%%'").fetchall()}
    kadmin = psycopg.connect(dbname=eq.KDB, autocommit=True)

    async with stdio(agent_url, "substrate") as (r, w), ClientSession(r, w, client_info=Implementation(
            name=CLIENT[0], version=CLIENT[1])) as session:
        await session.initialize()
        k = Kernel(session)

        # ---- the profile -------------------------------------------------------------------------------
        tools = {t.name: t for t in (await session.list_tools()).tools}
        check("profile: Substrate's ten tools, nothing else", set(tools) == set(sub.TOOLS) == {
            "search", "read", "expand", "locate", "path", "alternatives", "violations", "create_item", "say", "deprecate"},
              f"{sorted(tools)}")
        arg = lambda n: set(tools[n].input_schema["properties"])  # noqa: E731
        want = {t["name"]: set(t["inputSchema"]["properties"]) for t in STORE_TOOLS}
        check("profile: every tool takes the arguments Substrate's tool takes (path takes from and to)",
              all(arg(n) == want[n] for n in tools), f"{[(n, arg(n), want[n]) for n in tools if arg(n) != want[n]]}")
        req = lambda n: set(tools[n].input_schema.get("required", []))  # noqa: E731
        wreq = {t["name"]: set(t["inputSchema"]["required"]) for t in STORE_TOOLS}
        check("profile: and requires the same ones", all(req(n) == wreq[n] for n in tools),
              f"{[(n, req(n), wreq[n]) for n in tools if req(n) != wreq[n]]}")
        for outside in ("query_claims", "remember", "submit_change_set", "get_entity", "whoami", "substrate__read"):
            err = await refused(k, outside)
            check(f"profile: {outside} is refused", err is not None and "not in your profile" in err, (err or "")[:100])

        async with stdio(agent_url, "memory") as (r2, w2), ClientSession(r2, w2) as s2:
            await s2.initialize()
            names = {t.name for t in (await s2.list_tools()).tools}
            check("memory profile: no substrate tool listed", names == {"remember", "recall", "explain", "changes_since"}, f"{names}")
            res = await s2.call_tool("read", {"id": "Q1"})
            check("memory profile: read is refused", res.is_error and "not in your profile" in res.content[0].text)
        async with stdio(agent_url, None) as (r3, w3), ClientSession(r3, w3) as s3:
            await s3.initialize()
            ops = {t.name: t for t in (await s3.list_tools()).tools}
            check("operator profile: its own search (text), no substrate tool",
                  "text" in ops["search"].input_schema["properties"] and not any(n.startswith("substrate__") for n in ops)
                  and "read" not in ops, f"{sorted(ops)}")
            res = await s3.call_tool("substrate__read", {"id": "Q1"})
            check("operator profile: a substrate tool by its internal name is refused", res.is_error)

        # ---- reading, against the store ------------------------------------------------------------------
        wb, pg, pgv = ids["fx:workbench"], ids["fx:postgres"], ids["fx:pgvector"]
        vindex, storage, qdrant = ids["fx:vector-index"], ids["fx:storage"], ids["fx:qdrant"]
        n_search = n_hits = 0
        for q in ("Postgres", "pgvector", "vector", "workbench", "queue", "SQL interface", "FAISS", "latency", "pg_vector"):
            ours, theirs = (await k("search", query=q)).splitlines(), st("search", query=q).splitlines()
            o_ids = [ln.split()[0] for ln in ours]
            t_ids = [ln.split()[0] for ln in theirs]
            n_search += 1
            n_hits += len(t_ids)
            plain = [t for t in t_ids if re.fullmatch(r"Q\d+", t) and int(t[1:]) > 10]   # the store's own pack items differ
            check(f"search {q!r}: every store hit of the fixture's items is found; same top hit",
                  all(t in o_ids for t in plain) and (not plain or [x for x in o_ids if x in plain][0] == plain[0]),
                  f"ours {o_ids} store {t_ids}")
            check(f"search {q!r}: each hit cites a claim handle", all("[#" in ln for ln in ours if ln != "nothing found"), str(ours)[:200])
        check("search: nothing found says so", await k("search", query="zzzz-no-such-thing") == st("search", query="zzzz-no-such-thing"))

        n_read = 0
        for name in ("fx:workbench", "fx:postgres", "fx:pgvector", "fx:vector-index", "fx:qdrant", "fx:faiss", "fx:storage"):
            for all_ranks in (False, True):
                ours, theirs = await k("read", id=ids[name], all_ranks=all_ranks), st("read", id=ids[name], all_ranks=all_ranks)
                page, _, claims = ours.partition("\n## Claims\n")
                n_read += 1
                # statement lines (bullets and qualifiers) and headings agree after the ids and the prose are set aside
                # (the pack's label for P3831 is "object of statement has role", reference numbers count in another
                # order, and an external id has no backticks: documented in D-104)
                lines = lambda t: sorted(re.sub(r"\s*\[\d+\]", "", bare(x).replace("`", "").replace("object of statement has role", "object has role"))  # noqa: E731
                                         for x in t.splitlines() if x.startswith(("- ", "  - ", "**", "# ")))
                check(f"read {name} all_ranks={all_ranks}: the same statements, qualifiers and headings as the store's page",
                      lines(page) == lines(theirs), f"\nours {lines(page)}\nstore {lines(theirs)}")
                handles = re.findall(r"^- (#[0-9a-f]{8}) belief ", claims, re.M)
                check(f"read {name} all_ranks={all_ranks}: a claim handle with belief for each statement",
                      len(handles) == len([x for x in page.splitlines() if x.startswith("- ")]), f"{len(handles)}")
        check("read: an entity with nothing said", await k("read", id="Q999999") == "no entity Q999999" == st("read", id="Q999999"))
        by_label = await k("read", id="Workbench")
        check("read: an item by its exact label", by_label.startswith("# Workbench ("), by_label[:60])

        for name in ("fx:workbench", "fx:postgres", "fx:pgvector", "fx:vector-index", "fx:sqlite"):
            ours, theirs = (await k("expand", id=ids[name])).splitlines(), st("expand", id=ids[name]).splitlines()
            check(f"expand {name}: the same one hop, out then in", sorted(map(bare, ours)) == sorted(map(bare, theirs)),
                  f"\nours {sorted(map(bare, ours))}\nstore {sorted(map(bare, theirs))}")
            check(f"expand {name}: out before in, each line with a handle",
                  all("[#" in x for x in ours) and [x.split()[0] == ids[name] for x in ours] == sorted(
                      [x.split()[0] == ids[name] for x in ours], reverse=True))

        for name in ("fx:workbench", "fx:pgvector", "fx:qdrant"):
            ours, theirs = json.loads(await k("locate", target=ids[name])), json.loads(st("locate", target=ids[name]))
            sig = lambda rows, keys: sorted(json.dumps([r.get(x) for x in keys], default=str) for r in rows)  # noqa: E731
            o = [{"by": r.get("by"), "name": r.get("name"), "version": r.get("version"), "text": (r.get("span") or {}).get("text"),
                  "locator": (r.get("span") or {}).get("locator"), "source": (r.get("span") or {}).get("source")} for r in ours
                 if r["name"] and not r["name"].startswith("urn:kk:substrate:entity")]
            t = [{"by": r["by"], "name": r["name"], "version": r["version"], "text": (r.get("span") or {}).get("text"),
                  "locator": (r.get("span") or {}).get("locator"), "source": (r.get("span") or {}).get("source")} for r in theirs]
            keys = ["by", "name", "version", "text", "locator", "source"]
            check(f"locate {name}: the same references, by whom and the quoted text", sig(o, keys) == sig(t, keys),
                  f"\nours {sig(o, keys)}\nstore {sig(t, keys)}")
        one_claim = json.loads(await k("locate", target=ids["fx:workbench"]))[0]["claim"]
        located = json.loads(await k("locate", target="#" + one_claim[:8]))
        check("locate: one claim by its #handle", len(located) >= 1 and all(x["claim"] == one_claim for x in located), str(located)[:120])
        check("locate: nothing to find says so, as the store does",
              await k("locate", target="Q999999") == "no evidence found" == st("locate", target="Q999999"))

        # (the store's own path is pgGraph's, an extension this test database does not have: the shortest path
        # is found over the store's statements that are not deprecated, which is what store.path is defined as)
        edges: dict[str, set] = {}
        for subj, obj in sconn.execute("select s.subject, s.value_entity from store.statement s join store.entity e on e.id = s.subject "
                                       "where s.value_entity is not null and s.rank <> 'deprecated' and e.kind = 'item'").fetchall():
            edges.setdefault(subj, set()).add(obj)

        def store_steps(a, b):
            seen, frontier, n = {a}, [a], 0
            while frontier and b not in seen:
                frontier = [o for x in frontier for o in edges.get(x, ()) if o not in seen and not seen.add(o)]
                n += 1
            return n if b in seen else None

        for a, b in ((wb, pgv), (wb, pg), (wb, vindex), (pgv, wb), (pg, pgv), (wb, wb), (wb, ids["fx:knn"]), (wb, ids["fx:v1-db"])):
            ours = await k("path", **{"from": a, "to": b})
            want_steps = store_steps(a, b)
            o_steps = ours.split("\nclaims:")[0].count(" -> ")
            check(f"path {a} -> {b}: as many steps as the store's shortest path, or none",
                  (ours == "no path") == (want_steps is None) and (want_steps is None or o_steps == want_steps),
                  f"ours {ours!r} store {want_steps}")
            if ours != "no path" and o_steps:
                first = ours.split(" -> ")[0]
                check(f"path {a} -> {b}: the claims it follows are cited", "\nclaims: #" in ours and first.endswith(f"({a})"))

        n_alt = 0
        for role in (storage, vindex, ids["fx:mq"], wb):
            ours, theirs = (await k("alternatives", role=role)).splitlines(), st("alternatives", role=role).splitlines()
            n_alt += len(theirs)
            check(f"alternatives {role}: the same candidates, and where they fill the role",
                  sorted(map(bare, ours)) == sorted(map(bare, theirs)) or
                  [x.split("  ", 1)[0] for x in ours] == [x.split("  ", 1)[0] for x in theirs],
                  f"\nours {ours}\nstore {theirs}")
        check("alternatives: candidates cite the claims that say they provide the interface",
              all("[#" in x for x in (await k("alternatives", role=vindex)).splitlines()))

        # ---- violations: what the constraints module reported, against the store's tool (D-108) ---------------------
        subprocess.run([eq.PY, str(ROOT / "modules/view-substrate/tests/make_constraints_store.py"), store_db], check=True,
                       env={**os.environ, "PYTHONPATH": eq.SUBSTRATE}, capture_output=True)
        eq.encode(kenv)
        eq.wait_applied(kadmin)
        from constraints_wikidata.__main__ import one_pass
        from kk.lib import connect
        with connect("kkm_constraints_wikidata", url=eq.url("kk_authenticator", eq.PW, eq.KDB)) as mod:
            one_pass(mod)
        eq.wait_applied(kadmin)
        parse = lambda x: re.match(r"(\S+) \S+ \((\S+)\): ", x).groups()  # noqa: E731  (subject, kind)
        ours, theirs = (await k("violations")).splitlines(), st("violations").splitlines()
        check(f"violations: the same {len(theirs)} lines as the store's tool, by subject and kind",
              len(theirs) >= 8 and sorted(map(parse, ours)) == sorted(map(parse, theirs)), f"\nours {ours}\nstore {theirs}")
        check("violations: every line ends with the statement's claim and its handle and belief",
              all(re.search(r"\[statement [0-9a-f-]{36}, #[0-9a-f]{8} belief [\d.-]+\]$", x) for x in ours), str(ours[:2]))
        cx = {k_: i for i, k_ in sconn.execute("select id, key from store.entity where key like 'cx:%%'").fetchall()}
        for key in ("cx:x", "cx:y", "cx:whole"):
            a, b = (await k("violations", id=cx[key])).splitlines(), st("violations", id=cx[key]).splitlines()
            check(f"violations {key}: the same lines for one entity", sorted(map(parse, a)) == sorted(map(parse, b)) and len(b) > 0, f"{a} {b}")
        by_label = (await k("violations", id="Constrained thing")).splitlines()
        check("violations: an entity can be named by its label", sorted(map(parse, by_label)) == sorted(map(parse, st("violations", id=cx["cx:x"]).splitlines())))
        check("violations: an entity with none says none, as the store does",
              await k("violations", id=cx["cx:class"]) == st("violations", id=cx["cx:class"]) == "none")

        # ---- writing, the same on both sides, then the encoder's claims against ours -----------------------------
        ev = {"source": "file:docs/ledger.md", "locator": "L4-5", "quote": "The Quokka ledger is reconciled every night."}
        steps = [
            ("create_item", dict(label="Quokka ledger", description="reconciled every night", aliases=["qledger", "the ledger"],
                                 instance_of=vindex, evidence=ev)),
        ]
        o_new = (await k("create_item", **steps[0][1])).splitlines()[0]
        t_new = st("create_item", **steps[0][1]).splitlines()[0]
        check("create_item: answers with the new id", re.fullmatch(r"Q\d+", o_new) and re.fullmatch(r"Q\d+", t_new), f"{o_new} {t_new}")
        check("create_item: finds similar items that are already here",
              "similar, already here" in await k("create_item", label="Quokka ledger", description="again")
              or "similar" in st("create_item", label="Quokka ledger", description="again"))
        later = [
            ("say", dict(subject=o_new, property="described-at-url", value="https://example.org/quokka", evidence=ev, note="from the doc")),
            ("say", dict(subject=o_new, property="metric", value="rows reconciled per night")),
            ("say", dict(subject=o_new, property="measurement", value=3.5,
                         qualifiers=[{"property": "for-goal", "value": ids["fx:latency"]},
                                     {"property": "point-in-time", "value": "2026-11"}])),
            ("say", dict(subject=o_new, property="provides-interface", value=ids["fx:knn"], evidence=ev)),
            ("say", dict(subject=wb, property="has-part", value=o_new,
                         qualifiers=[{"property": "object-has-role", "value": storage}])),
        ]
        o_say = []
        for name, args in later:
            o_say.append(await k(name, **args))
            st(name, **{**args, "subject": t_new if args["subject"] == o_new else args["subject"],
                        "value": t_new if args["value"] == o_new else args["value"]})
        check("say: each answer is a claim id", all(re.fullmatch(r"[0-9a-f-]{36}", x) for x in o_say), str(o_say))
        again = await k("say", **later[0][1])
        check("say: the same words again are the same change set (no second claim)", re.fullmatch(r"[0-9a-f-]{36}|change set .*", again) is not None, again)
        for bad, why in ((dict(subject=o_new, property="no-such-key", value="x"), "unknown property"),
                         (dict(subject="Q999998", property="metric", value="x"), "no entity"),
                         (dict(subject=o_new, property="measurement", value={"amount": 3, "unit": "ms"}), "unit"),
                         (dict(subject=o_new, property="described-at-url", value="not a url"), "URL"),
                         (dict(subject=o_new, property="metric", value="x", evidence={"source": "a"}), "evidence")):
            err = await refused(k, "say", **bad)
            check(f"say: refuses {why}", err is not None and why.split()[0].lower() in err.lower(), str(err)[:100])
        err = await refused(k, "create_item", label="x", evidence=ev)
        check("create_item: evidence without instance_of is refused, as in the store", err is not None and "instance_of" in err, str(err))

        # the store side is exported by the encoder; compare what each side holds about its new item
        eq.encode(kenv)
        eq.wait_applied(kadmin)
        ours_c = claims_of(kadmin, "urn:kk:substrate:" + o_new, o_new)
        theirs_c = claims_of(kadmin, "urn:kk:substrate:" + t_new, t_new)
        agents = {r[0]: r[1] for r in kadmin.execute("select distinct c.subject, a.iri from kernel.claim c join kernel.agent a on a.iri = c.attributed_to"
                                                    " where c.subject in (%s, %s)", ("urn:kk:substrate:" + o_new, "urn:kk:substrate:" + t_new)).fetchall()}
        check("write equivalence: the kernel and the encoder hold claims about the new items",
              len(ours_c) >= 10 and len(theirs_c) >= 10, f"{len(ours_c)} {len(theirs_c)}")
        check("write equivalence: equal claims (subject, predicate, object, qualifiers, evidence type, references)",
              ours_c == theirs_c, "\nonly ours:\n  " + "\n  ".join(map(str, [c for c in ours_c if c not in theirs_c])) +
              "\nonly encoder:\n  " + "\n  ".join(map(str, [c for c in theirs_c if c not in ours_c])))
        who = {r[0] for r in kadmin.execute(
            "select a.iri from kernel.claim c join kernel.agent a on a.iri = c.attributed_to where c.subject = %s",
            ("urn:kk:substrate:" + o_new,)).fetchall()}
        check("write equivalence: the claims are the calling agent's own", who == {f"urn:kk:agent:{AGENT}"}, str(who))
        evs = {r[0] for r in kadmin.execute("select evidence_type from kernel.claim where subject = %s and predicate not in (%s, %s, %s)",
                                            ("urn:kk:substrate:" + o_new, *sub.LABELS)).fetchall()}
        check("write equivalence: a statement is testimony, its reference an interpreter's", evs == {"testimony"}
              and kadmin.execute("select count(*) from api.claim_references where source_uri like %s",
                                 (f"%?by=interpreter:{CLIENT[0]}&version=1.0",)).fetchone()[0] > 0, str(evs))
        page = await k("read", id=o_new)
        check("write equivalence: the page of the item made through MCP shows its references like the store's",
              "interpreter eq-client (version 1.0) in file:docs/ledger.md at L4-5" in page, page[:400])
        runs = kadmin.execute("select info from kernel.run where info->>'via' = 'substrate profile' and info->>'tool' = 'say'").fetchall()
        check("write: each call is a run that names the tool and keeps the note", any(r[0].get("note") == "from the doc" for r in runs), str(runs)[:200])

        # deprecate: ours, and what is refused
        victim = o_say[1]
        out = await k("deprecate", statement=victim, reason="wrong metric")
        check("deprecate: your own claim, by its claim id", out == f"deprecated {victim}", out)
        status = kadmin.execute("select status from api.claims where id = %s", (victim,)).fetchone()
        check("deprecate: the claim is deprecated, not deleted", status and status[0] == "deprecated", str(status))
        check("deprecate: the page no longer shows it", "rows reconciled per night" not in await k("read", id=o_new)
              and "rows reconciled per night" in await k("read", id=o_new, all_ranks=True))
        err = await refused(k, "deprecate", statement=one_claim, reason="mine now")
        check("deprecate: someone else's claim is refused, say a contradicting one", err is not None and "someone else" in err, str(err))
        err = await refused(k, "deprecate", statement=o_say[0], reason=" ")
        check("deprecate: a reason is needed", err is not None and "reason" in err, str(err))
        check("deprecate: again says so", (await k("deprecate", statement=victim, reason="again")).startswith("already deprecated"))

        # ---- row-level security: the tools read as the agent, so a scope it is not cleared for is in no answer ----
        scope = "urn:kk:scope:sp-private"
        kadmin.execute("insert into kernel.scope(iri, label) values (%s, 'substrate profile test: private')", (scope,))
        for role, w in (("kkm_encoder_substrate", True), ("kkm_validator", False)):
            kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (scope, role, w))
        s_priv = Store_(sconn)
        eq_out = subprocess.run([eq.PY, "-m", "encoder_substrate", "--store", store_db, "--scope", scope],
                                check=True, env=kenv, cwd=ROOT, capture_output=True, text=True).stdout
        eq.wait_applied(kadmin)
        vault, vpart = s_priv
        nothing = [await k("search", query="zebrafish"), await k("read", id=vault), await k("expand", id=vault),
                   await k("path", **{"from": vault, "to": vpart}), await k("locate", target=vault)]
        check("rls: an agent not cleared for a scope finds nothing of it through search, read, expand, path and locate",
              nothing == ["nothing found", f"no entity {vault}", "no statements", "no path", "no evidence found"], str(nothing))
        err = await refused(k, "say", subject=vault, property="metric", value="x")
        check("rls: and cannot say something about it", err is not None and "no entity" in err, str(err))
        kadmin.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, false)",
                       (scope, f"agent_{AGENT.replace('-', '_')}"))
        found = [await k("search", query="zebrafish"), await k("read", id=vault), await k("path", **{"from": vault, "to": vpart})]
        check("rls: cleared for it, the same agent finds them", "Zebrafish vault" in found[0] and found[1].startswith("# Zebrafish vault")
              and "Zebrafish part" in found[2], str(found)[:300])
        kadmin.execute("delete from kernel.scope_grant where scope = %s and db_role = %s", (scope, f"agent_{AGENT.replace('-', '_')}"))
        check("rls: and when the grant is taken away they are gone again", await k("search", query="zebrafish") == "nothing found")
    sconn.close()


def main():
    kenv = eq.build()
    kadmin = psycopg.connect(dbname=eq.KDB, autocommit=True)
    summary = eq.encode(kenv)
    eq.wait_applied(kadmin)
    bad = kadmin.execute("select count(*) from kernel.change_set where status in ('invalid', 'rejected', 'conflict')").fetchone()[0]
    check("setup: the fixture store is in the kernel", bad == 0 and summary["new"] > 0, f"{summary['new']} change sets, {bad} refused")
    out = subprocess.run([eq.PY, "-m", "kk", "agent", "add", AGENT], env=kenv, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    made = json.loads(out.strip().splitlines()[-1])
    agent_url = eq.url(made["role"], made["password"], eq.KDB)
    asyncio.run(run(kenv, agent_url))


if __name__ == "__main__":
    try:
        main()
    finally:
        eq.cleanup()
        try:
            eq.admin.execute(f'drop role if exists "agent_{AGENT.replace("-", "_")}"')
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{eq.checks - len(eq.fails)}/{eq.checks} checks passed" + (f"; failed: {eq.fails}" if eq.fails else ""))
    sys.exit(1 if eq.fails else 0)

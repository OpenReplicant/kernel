"""D-83 spike: does a live AGE graph, kept in sync inside the kernel's apply transaction, with detectors
that record formed/dissolved events, hold up? Correctness (supersede, redaction, a failed apply, row-level
security, read-only) and cost (bulk load and per-apply latency, with and without the graph and detectors).

Runs against a scratch Postgres with AGE (see Dockerfile here): docker run ... -p 55432:5432 ... -c
shared_preload_libraries=age. Loads the kernel (001, 002), the vocabulary packs, then graph.sql.
Usage: python docs/spikes/D83-age/spike.py [orgs-for-the-benchmark]
"""
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DSN = "host=localhost port=55432 user=postgres"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
ORG = "http://www.w3.org/ns/org#"
HOLDS, REPORTS, POST = ORG + "holds", ORG + "reportsTo", ORG + "Post"
UNHELD = f"""MATCH (p:Entity)-[t:Claim {{p: '{RDF_TYPE}'}}]->(:Entity {{iri: '{POST}'}})
WHERE p.iri = $anchor AND NOT EXISTS((p)<-[:Claim {{p: '{HOLDS}'}}]-(:Entity))
RETURN p.iri, [p.iri], [t.id]"""
CHAIN = f"""MATCH (x:Entity)-[h1:Claim {{p: '{HOLDS}'}}]->(a:Entity)-[r:Claim {{p: '{REPORTS}'}}]->(b:Entity)<-[h2:Claim {{p: '{HOLDS}'}}]-(y:Entity)"""
MANAGER = "\nUNION\n".join(f"{CHAIN} WHERE {v}.iri = $anchor RETURN x.iri + '|' + y.iri, [x.iri, a.iri, b.iri, y.iri], [h1.id, r.id, h2.id]"
                              for v in "xaby")
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail if not ok else "")


def psql(db, *files):
    for f in files:
        out = subprocess.run(["docker", "exec", "-i", "agespike", "psql", "-U", "postgres", "-d", db, "-q", "-v",
                              "ON_ERROR_STOP=1"], input=Path(f).read_text(), capture_output=True, text=True)
        if out.returncode:
            raise SystemExit(f"{f}: {out.stderr[-2000:]}")


def fresh(db, graph: bool):
    with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
        c.execute(f"drop database if exists {db} with (force)")
        c.execute(f"create database {db}")
    psql(db, ROOT / "kernel/schema/001_kernel.sql", ROOT / "kernel/schema/002_bootstrap.sql",
         ROOT / "modules/pack-loader/migration.sql", ROOT / "modules/pack-loader/packs.sql", *([HERE / "graph.sql"] if graph else []))
    c = psycopg.connect(DSN + f" dbname={db}", autocommit=True)
    c.execute("set search_path = ag_catalog, public")   # AGE resolves its operators by search_path
    for r, g in [("t_agent", "kk_agent"), ("t_cleared", "kk_agent"), ("t_validator", "kk_validator"),
                 ("t_steward", "kk_steward"), ("t_secret", None)]:
        c.execute(f"do $$ begin create role {r} nologin; exception when duplicate_object then null; end $$")
        if g:
            c.execute(f"grant {g} to {r}")
    c.execute("grant t_secret to t_cleared, t_validator")
    c.execute("""insert into kernel.agent(iri, db_role, kind, label) values
                 ('urn:kk:agent:t-agent', 't_agent', 'software', 'agent'),
                 ('urn:kk:agent:t-cleared', 't_cleared', 'software', 'cleared agent'),
                 ('urn:kk:agent:t-validator', 't_validator', 'software', 'validator'),
                 ('urn:kk:agent:t-steward', 't_steward', 'person', 'steward') on conflict do nothing""")
    c.execute("insert into kernel.scope values ('urn:kk:scope:secret', 'secret') on conflict do nothing")
    c.execute("insert into kernel.scope_grant values ('urn:kk:scope:secret', 't_secret', true, true) on conflict do nothing")
    return c


def claim(s, p, o, ref=None):
    op = {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": o}, "evidence_type": "extracted",
          "references": [{"source_uri": "urn:kk:message:spike:1", "quote": "said so"}]}
    return {**op, "ref": ref} if ref else op


def apply(c, ops, role="t_agent", scope="urn:kk:scope:default"):
    """Submit as `role`, validate as the validator; returns (status, ms for validate+apply+commit)."""
    agent = {"t_agent": "urn:kk:agent:t-agent", "t_cleared": "urn:kk:agent:t-cleared"}[role]
    c.execute(f"set role {role}")
    run = c.execute("select api.start_run(null, null, '[]'::jsonb, '{}'::jsonb)").fetchone()[0]
    cs = c.execute("select api.submit_change_set(%s)", (Jsonb({
        "idempotency_key": f"spike:{time.time_ns()}", "attributed_to": agent, "generated_by": str(run),
        "target_scope": scope, "ops": ops}),)).fetchone()[0]["id"]
    c.execute("set role t_validator")
    t = time.perf_counter()
    status = c.execute("select api.record_validation(%s, true, '{}')", (cs,)).fetchone()[0]
    ms = (time.perf_counter() - t) * 1000
    c.execute("reset role")
    return status, ms, cs


def events(c, since):
    return [(d.rsplit(":", 1)[1], k, key) for d, k, key in c.execute(
        "select detector, kind, key from kernel.event where id > %s order by id", (since,)).fetchall()]


def last(c):
    return c.execute("select coalesce(max(id), 0) from kernel.event").fetchone()[0]


def edges(c, role=None):
    if role:
        c.execute(f"set role {role}")
    n = c.execute("""select count(*) from ag_catalog.cypher('kkg', $$ MATCH ()-[e:Claim]->() RETURN e $$) as (e ag_catalog.agtype)""").fetchone()[0]
    c.execute("reset role")
    return n


def short(i):
    return i.rsplit(":", 1)[1]


def register(c):
    c.execute("delete from kernel.detector_match; delete from kernel.event; delete from kernel.detector")
    c.execute("insert into kernel.detector values ('urn:kk:detector:unheld-post', 'shape', null, %s), "
              "('urn:kk:detector:manager-of', 'shape', null, %s), "
              "('urn:kk:detector:holds', 'claim', %s, null), "
              "('urn:kk:detector:text', 'artifact', '{\"media_type\": \"text/%%\"}', null)",
              (UNHELD, MANAGER, Jsonb({"p": HOLDS})))


def correctness():
    c = fresh("spike", graph=True)
    register(c)
    o = "urn:kk:org:acme:post:"
    p = "urn:kk:person:acme:"
    lee, dana, sam = p + "lee", p + "dana", p + "sam"
    buyer, lead, director = o + "buyer", o + "lead", o + "director"

    n = last(c)
    apply(c, [claim(x, RDF_TYPE, POST) for x in (buyer, lead, director)])
    e = events(c, n)
    check("posts typed: each unheld post forms a match", sorted(e) == sorted(
        [("unheld-post", "formed", x) for x in (buyer, lead, director)]), e)

    n = last(c)
    st, _, _ = apply(c, [claim(lee, HOLDS, buyer, "h1"), claim(dana, HOLDS, lead, "h2"), claim(sam, HOLDS, director, "h3")])
    e = events(c, n)
    check("holds added: unheld-post dissolves, the claim detector forms", st == "applied" and
          sorted(k for d, k, _ in e if d == "unheld-post") == ["dissolved"] * 3 and
          sorted(k for d, k, _ in e if d == "holds") == ["formed"] * 3, e)
    holds_sam = c.execute("select id from kernel.claim where subject = %s and predicate = %s", (sam, HOLDS)).fetchone()[0]

    n = last(c)
    apply(c, [claim(buyer, REPORTS, lead), claim(lead, REPORTS, director)])
    e = events(c, n)
    check("two-hop shape forms across change sets: manager-of lee|dana and dana|sam",
          sorted((k, "|".join(map(short, key.split("|")))) for d, k, key in e if d == "manager-of") ==
          [("formed", "dana|sam"), ("formed", "lee|dana")], e)

    old = c.execute("select id from kernel.claim where subject = %s and predicate = %s", (buyer, REPORTS)).fetchone()[0]
    n = last(c)
    apply(c, [claim(buyer, REPORTS, director, "new"),
              {"op": "set_status", "claim": str(old), "status": "deprecated", "reason": "superseded", "replaced_by": "new"}])
    e = events(c, n)
    check("supersede (the buyer now reports to the director): lee|dana dissolves, lee|sam forms",
          sorted((k, "|".join(map(short, key.split("|")))) for d, k, key in e if d == "manager-of") ==
          [("dissolved", "lee|dana"), ("formed", "lee|sam")], e)
    check("supersede: the old edge left the graph, the new one is in it",
          c.execute("""select count(*) from kkg."Claim" where properties operator(ag_catalog.@>) %s::text::ag_catalog.agtype""",
                    (json.dumps({"id": str(old)}),)).fetchone()[0] == 0 and edges(c) == 8, edges(c))

    c.execute("set role t_steward")
    run = c.execute("select api.start_run(null, null, '[]'::jsonb, '{}'::jsonb)").fetchone()[0]
    n = last(c)
    c.execute("select api.redact('claim', %s, 'spike: the person asked to be forgotten', %s)", (str(holds_sam), run))
    c.execute("reset role")
    e = events(c, n)
    check("redaction of sam holds director: edge gone, unheld-post forms, both manager-of matches with sam dissolve, "
          "the claim detector dissolves",
          sorted(e) == sorted([("unheld-post", "formed", director), ("holds", "dissolved", str(holds_sam)),
                               ("manager-of", "dissolved", f"{dana}|{sam}"), ("manager-of", "dissolved", f"{lee}|{sam}")])
          and edges(c) == 7, (e, edges(c)))

    n, before = last(c), edges(c)
    st, _, _ = apply(c, [claim(sam, HOLDS, director), claim(sam, "urn:kk:unknown-predicate", director)])
    check("a change set that fails mid-way leaves no edge and no event", st == "invalid" and edges(c) == before
          and events(c, n) == [], (st, events(c, n)))

    n = last(c)
    st, _, _ = apply(c, [claim("urn:kk:org:acme:post:spy", RDF_TYPE, POST),
                         claim("urn:kk:person:acme:x", HOLDS, "urn:kk:org:acme:post:spy")], "t_cleared", "urn:kk:scope:secret")
    sees = lambda r: (c.execute(f"set role {r}"), c.execute(  # noqa: E731
        "select count(*) from kernel.event where id > %s", (n,)).fetchone()[0], c.execute("reset role"))[1]
    check("row-level security: an uncleared agent sees neither the secret edges (Cypher) nor their events; a cleared one does",
          st == "applied" and edges(c, "t_agent") == edges(c) - 2 and edges(c, "t_cleared") == edges(c)
          and sees("t_agent") == 0 and sees("t_cleared") == 1, (st, edges(c, "t_agent"), edges(c), sees("t_agent"), sees("t_cleared")))

    c.execute("set role t_agent")
    try:
        c.execute("""select * from ag_catalog.cypher('kkg', $$ CREATE (:Entity {iri: 'x'})-[:Claim {p: 'x'}]->(:Entity {iri: 'y'}) $$) as (r ag_catalog.agtype)""")
        ro = False
    except psycopg.Error as err:
        ro = "permission denied" in str(err)
    c.execute("reset role")
    check("the graph is read-only to agents (Cypher CREATE is refused)", ro)

    n = last(c)
    sha = "ab" * 32
    apply(c, [{"op": "add_artifact", "sha256": sha, "media_type": "text/plain", "byte_size": 3, "location": "mem://x"}])
    check("an artifact detector (media type text/%) forms on a new artifact", events(c, n) == [("text", "formed", sha)], events(c, n))

    c.execute("set role t_agent")
    rows = c.execute(f"""select * from ag_catalog.cypher('kkg', $$ MATCH (x)-[:Claim {{p: '{HOLDS}'}}]->(a)-[:Claim {{p: '{REPORTS}'}}]->(b)
                         RETURN x.iri, b.iri $$) as (x ag_catalog.agtype, b ag_catalog.agtype)""").fetchall()
    c.execute("reset role")
    check("agents query the live graph in openCypher (who reports to whose post)", len(rows) == 2, rows)
    c.close()


def org_ops(i):
    base, ops = f"urn:kk:org:o{i}", []
    for j in range(10):
        post, unit = f"{base}:post:p{j}", f"{base}:unit:u{j % 3}"
        ops += [claim(post, RDF_TYPE, POST), claim(f"urn:kk:person:o{i}:x{j}", HOLDS, post), claim(post, ORG + "postIn", unit)]
        if j:
            ops.append(claim(post, REPORTS, f"{base}:post:p{(j - 1) // 3}"))
    return ops                                       # 39 edges per organisation


def bench(orgs):
    results = {}
    for name, graph in [("plain kernel", False), ("graph", True)]:
        c = fresh("bench", graph)
        c.execute("set role kk_owner")
        t = time.perf_counter()
        for b in range(0, orgs, 10):
            c.execute("select kernel.install_ops(%s, 'urn:kk:scope:default', %s)",
                      (f"bench:{b}", Jsonb([op for i in range(b, b + 10) for op in org_ops(i)])))
        load = time.perf_counter() - t
        c.execute("reset role")
        configs = [(name, None)] if not graph else [("graph, no detectors", None), ("graph + 2 shape + 1 claim detectors", register)]
        for label, setup in configs:
            if setup:
                setup(c)
            ms = [apply(c, org_ops(orgs + k + (0 if setup is None else 1000))[:10])[1] for k in range(60)][10:]
            results[label] = (load, statistics.median(ms), sorted(ms)[int(len(ms) * .95)])
        if graph:
            results["edges"] = edges(c)
        c.close()
    claims = orgs * 39
    print(f"\nbenchmark: {orgs} organisations, {claims} claims loaded in change sets of 390; then 50 change sets of 10 claims")
    for k, v in results.items():
        if k != "edges":
            print(f"  {k:40s} load {v[0]:6.1f} s ({v[0] / claims * 1000:.2f} ms/claim)   apply p50 {v[1]:6.1f} ms   p95 {v[2]:6.1f} ms")
    print(f"  edges in the graph: {results['edges']}")


if __name__ == "__main__":
    correctness()
    print(f"{sum(checks)}/{len(checks)} checks passed")
    bench(int(sys.argv[1]) if len(sys.argv) > 1 else 1300)
    sys.exit(0 if all(checks) else 1)

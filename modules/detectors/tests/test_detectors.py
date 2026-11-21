#!/usr/bin/env python3
"""The detector module on a throwaway kernel (M6-T0, D-110), on the D-83 spike's scenario (posts nobody holds,
who manages whom through two posts, a claim detector) without the graph: the detectors are SQL over api.edges
and api.claims.

Checks: the events of the spike's scenario (typing, holding, a two-hop shape across change sets, a supersede,
a redaction); the cursor resumes after a restart without missing or repeating; a failing detector stops only
itself and is recorded; clearance (a detector owned by an uncleared role never matches secret claims; a
cleared one does, in its scope); idempotent re-runs; one pass at a time; a match that forms and dissolves
between two passes is never seen (level-triggered, documented). Drops the database at the end.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from harness import Kernel, connect, skip  # noqa: E402

try:
    from detectors.engine import Detector, Engine
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")

ORG = "http://www.w3.org/ns/org#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
HOLDS, REPORTS, POST = ORG + "holds", ORG + "reportsTo", ORG + "Post"
SECRET = "urn:kk:scope:det-secret"
ANCHOR = "(%(anchors)s::text[] is null or {nodes} && %(anchors)s::text[])"

UNHELD = f"""select p.subject as key, array[p.subject] as nodes, array[p.id] as claims from api.claims p
  where p.predicate = '{RDF_TYPE}' and p.object_entity = '{POST}' and p.status <> 'deprecated' and p.redacted_at is null
    and {ANCHOR.format(nodes="array[p.subject]")}
    and not exists (select 1 from api.edges h where h.predicate = '{HOLDS}' and h.object = p.subject)"""
MANAGER = f"""select h1.subject || '|' || h2.subject as key, array[h1.subject, h1.object, r.object, h2.subject] as nodes,
         array[h1.claim_id, r.claim_id, h2.claim_id] as claims
  from api.edges h1 join api.edges r on r.subject = h1.object and r.predicate = '{REPORTS}'
  join api.edges h2 on h2.object = r.object and h2.predicate = '{HOLDS}'
  where h1.predicate = '{HOLDS}' and {ANCHOR.format(nodes="array[h1.subject, h1.object, r.object, h2.subject]")}"""
HOLDING = f"""select e.claim_id::text as key, array[e.subject, e.object] as nodes, array[e.claim_id] as claims from api.edges e
  where e.predicate = '{HOLDS}' and {ANCHOR.format(nodes="array[e.subject, e.object]")}"""
SECRET_POSTS = f"""select p.subject as key, array[p.subject] as nodes, array[p.id] as claims from api.claims p
  where p.predicate = '{RDF_TYPE}' and p.object_entity = '{POST}' and p.scope = '{SECRET}' and p.status <> 'deprecated'
    and {ANCHOR.format(nodes="array[p.subject]")}"""

ME, AGENT, MODULE = "kkm_detectors", "urn:kk:agent:detectors", "urn:kk:module:detectors"
o = "urn:kk:org:acme:post:"
buyer, lead, director = o + "buyer", o + "lead", o + "director"
lee, dana, sam = "urn:kk:person:acme:lee", "urn:kk:person:acme:dana", "urn:kk:person:acme:sam"
K = Kernel("det")


def claim(s, p, obj, n=0):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": obj}, "evidence_type": "deterministic",
            "references": [{"source_uri": f"urn:kk:test:source{n}", "quote": "as the test says"}]}


def detectors():
    return [Detector("unheld-post", ME, sql=UNHELD), Detector("manager-of", ME, sql=MANAGER),
            Detector("holds", ME, sql=HOLDING)]


def new_engine(ds=None, **kw):
    return Engine(ds or detectors(), ME, AGENT, MODULE, **kw)


def go(engine, conn):
    """One pass, then the change sets it made are applied."""
    out = engine.run_pass(conn)
    K.wait_applied()
    return out


def runs(status):
    return K.k.execute("select info, cost from kernel.run where agent = %s and status = %s order by started_at",
                       (AGENT, status)).fetchall()


def main():
    K.start()
    conn = connect(ME)
    eng = new_engine(full_every=1000)
    check = K.check

    # ---- the spike's scenario
    first = go(eng, conn)
    check("a first pass on an empty kernel is a full look and records nothing", all(r["formed"] == 0 for r in first.values()), str(first))
    n = K.seq()
    K.say([claim(p, RDF_TYPE, POST) for p in (buyer, lead, director)])
    go(eng, conn)
    ev = K.events(n)
    check("typing three posts forms three unheld-post matches",
          ev == sorted([("unheld-post", "formed", x) for x in (buyer, lead, director)], key=lambda e: (e[1], e[2])), str(ev))

    n = K.seq()
    K.say([claim(lee, HOLDS, buyer), claim(dana, HOLDS, lead), claim(sam, HOLDS, director)])
    go(eng, conn)
    ev = K.events(n)
    check("holding: unheld-post dissolves for each post, the claim detector forms for each claim",
          [e for e in ev if e[0] == "unheld-post"] == sorted([("unheld-post", "dissolved", x) for x in (buyer, lead, director)], key=lambda e: (e[1], e[2]))
          and sum(1 for e in ev if e[:2] == ("holds", "formed")) == 3 and not any(e[0] == "manager-of" for e in ev), str(ev))

    n = K.seq()
    K.say([claim(buyer, REPORTS, lead)])
    go(eng, conn)
    check("a two-hop shape forms across change sets", K.events(n) == [("manager-of", "formed", f"{lee}|{dana}")], str(K.events(n)))

    n = K.seq()
    old = K.k.execute("select id from kernel.claim where subject = %s and predicate = %s", (buyer, REPORTS)).fetchone()["id"]
    K.say([claim(buyer, REPORTS, director) | {"ref": "n"},
           {"op": "set_status", "claim": str(old), "status": "deprecated", "reason": "superseded", "replaced_by": "n"}])
    go(eng, conn)
    check("a supersede dissolves lee|dana and forms lee|sam (the change set only retracts and adds)",
          K.events(n) == [("manager-of", "dissolved", f"{lee}|{dana}"), ("manager-of", "formed", f"{lee}|{sam}")], str(K.events(n)))

    # ---- level-triggered: a match that forms and dissolves between two passes is never seen
    n = K.seq()
    K.say([claim("urn:kk:person:acme:kim", HOLDS, lead)])
    kim_claim = K.k.execute("select id from kernel.claim where subject = 'urn:kk:person:acme:kim'").fetchone()["id"]
    K.say([{"op": "set_status", "claim": str(kim_claim), "status": "deprecated", "reason": "retracted"}])
    go(eng, conn)
    check("a match that formed and dissolved between passes is missed (level-triggered)",
          not any("kim" in e[2] for e in K.events(n)) and not any(e[0] == "holds" and e[1] == "formed" and e[2] == str(kim_claim) for e in K.events(n)), str(K.events(n)))

    # ---- redaction has no applied row: the periodic full look finds it
    n = K.seq()
    sam_holds = K.k.execute("select id from kernel.claim where subject = %s and predicate = %s", (sam, HOLDS)).fetchone()["id"]
    K.k.execute("set role person_owner")
    rid = K.k.execute("select api.start_run(null, null, '[]', '{}') as r").fetchone()["r"]
    K.k.execute("select api.redact('claim', %s, 'the person asked to be forgotten', %s)", (str(sam_holds), rid))
    K.k.execute("reset role")
    check("redaction leaves no applied row, so an incremental pass sees nothing", go(eng, conn)["unheld-post"]["status"] == "idle")
    eng.full_every = 1       # every pass is a full look
    go(eng, conn)
    ev = K.events(n)
    check("redaction, on the next full look: unheld-post forms, the claim detector and both sam matches dissolve",
          ("unheld-post", "formed", director) in ev and ("holds", "dissolved", str(sam_holds)) in ev
          and ("manager-of", "dissolved", f"{lee}|{sam}") in ev, str(ev))
    eng.full_every = 1000

    # ---- idempotent: a full look again changes nothing
    before = K.k.execute("select count(*) as n from kernel.change_set where attributed_to = %s", (AGENT,)).fetchone()["n"]
    full = new_engine(full_every=1)
    go(full, conn)
    go(full, conn)
    check("running again, in full, submits nothing", K.k.execute("select count(*) as n from kernel.change_set where attributed_to = %s", (AGENT,)).fetchone()["n"] == before)
    check("every event's change set carries the premises: nothing is an event without a claim it was made from",
          K.k.execute("select count(*) as n from kernel.claim c join kernel.change_set s on s.id = c.change_set "
                      "where s.attributed_to = %s and not exists (select 1 from kernel.claim_derivation d where d.claim_id = c.id)", (AGENT,)).fetchone()["n"] == 0)

    # ---- the cursor resumes after a restart without missing or repeating
    stored = K.k.execute("select max((info->>'cursor')::bigint) as c from kernel.run where agent = %s and status = 'succeeded' and info->>'detector' = 'unheld-post'", (AGENT,)).fetchone()["c"]
    check("the cursor is stored in the runs", stored is not None and stored >= 1, str(stored))
    n = K.seq()
    again = new_engine(full_every=1000)          # a restart: nothing in memory
    out = go(again, conn)
    check("restarted with nothing new (bar its own events): nothing forms or dissolves, nothing repeats",
          all(r["formed"] == r["dissolved"] == 0 for r in out.values()) and K.events(n) == [], str(out))
    check("and with nothing at all new, it is idle", all(r["status"] == "idle" for r in go(again, conn).values()))
    K.say([claim("urn:kk:org:acme:post:clerk", RDF_TYPE, POST)])         # applied while no engine ran
    n2 = n
    again = new_engine(full_every=1000)
    go(again, conn)
    check("a change made while it was down is seen after the restart, once",
          K.events(n2) == [("unheld-post", "formed", "urn:kk:org:acme:post:clerk")], str(K.events(n2)))
    go(new_engine(full_every=1000), conn)
    check("and a second restart does not repeat it", K.events(n2) == [("unheld-post", "formed", "urn:kk:org:acme:post:clerk")])

    # ---- a failing detector stops only itself
    bad = [Detector("broken", ME, sql="select nope from nowhere"), Detector("ghost-owner", "kkm_no_such_role", sql=UNHELD),
           Detector("no-claims", ME, sql="select 'x' as key, array['urn:x']::text[] as nodes, array[]::uuid[] as claims"),
           Detector("unheld-post", ME, sql=UNHELD)]
    n = K.seq()
    ok_engine = new_engine(bad, full_every=1000)
    K.say([claim("urn:kk:org:acme:post:intern", RDF_TYPE, POST)])
    out = go(ok_engine, conn)
    check("a detector with broken SQL, one with an owner that does not exist and one that names no claims fail; the others go on",
          [out[k]["status"] for k in ("broken", "ghost-owner", "no-claims")] == ["failed"] * 3 and out["unheld-post"]["status"] == "ok", str(out))
    check("the one that went on recorded its event", ("unheld-post", "formed", "urn:kk:org:acme:post:intern") in K.events(n), str(K.events(n)))
    fails = runs("failed")
    check("each failure is a failed run that says why", {r["info"]["detector"] for r in fails} >= {"broken", "ghost-owner", "no-claims"}
          and all(r["cost"].get("error") for r in fails), str(fails))
    out = go(ok_engine, conn)
    check("a failing detector fails again, it does not advance", out["broken"]["status"] == "failed")

    # ---- one pass at a time
    other = connect(ME)
    other.execute("select pg_try_advisory_lock(hashtext(%s))", (AGENT,))
    check("while another pass holds the lock, a pass does nothing", new_engine().run_pass(conn) == {"status": "busy"})
    other.close()
    check("and when it is gone, passes run again", new_engine().run_pass(conn).get("status") != "busy")
    K.wait_applied()

    # ---- clearance
    K.k.execute("insert into kernel.scope(iri, label) values (%s, 'detector test: secret')", (SECRET,))
    for role, rw in (("kkm_encoder_substrate", True), ("kkm_watcher_spans", False), ("kkm_validator", False), (ME, True)):
        K.k.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (SECRET, role, rw))
    K.k.execute("delete from kernel.scope_grant where scope = %s and db_role = %s", (SECRET, ME))   # the module itself: not cleared yet
    K.say([claim("urn:kk:org:acme:post:spy", RDF_TYPE, POST)], SECRET)
    uncleared = Detector("secret-uncleared", "kkm_anomaly", sql=SECRET_POSTS)
    cleared = Detector("secret-cleared", "kkm_watcher_spans", sql=SECRET_POSTS, scope=SECRET)
    n = K.seq()
    ce = new_engine([uncleared, cleared], full_every=1000)
    out = go(ce, conn)
    check("a detector owned by a role not cleared for the secret scope matches nothing there",
          out["secret-uncleared"]["formed"] == 0 and not K.events(n, "secret-uncleared"), str(out))
    check("a cleared owner's detector forms, but the module that writes its events is not cleared to write there: it fails, with no event",
          out["secret-cleared"]["status"] == "failed" and not K.events(n, "secret-cleared"), str(out))
    K.k.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, true)", (SECRET, ME))
    out = go(ce, conn)
    check("granted, it forms in the secret scope", K.events(n, "secret-cleared") == [("secret-cleared", "formed", "urn:kk:org:acme:post:spy")], str(out))
    check("its events are in the secret scope", K.k.execute(
        "select count(*) as n from kernel.claim where subject like 'urn:kk:event:secret-cleared:%%' and scope <> %s", (SECRET,)).fetchone()["n"] == 0)
    check("and still nothing for the uncleared detector, which never saw it", not K.events(n, "secret-uncleared"))

    K.finish("D-110 detector database checks passed")


try:
    main()
finally:
    K.stop()

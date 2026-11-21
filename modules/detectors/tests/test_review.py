#!/usr/bin/env python3
"""The adversarial review's findings on the detector module (D-110), each as the attack that found it, on a
throwaway kernel: B1 clearance leak, B2 forged events, B3 role switching in a detector's query, B4 belief and
verification anchors, B5 a refused change set, B7 a squatted idempotency key, B8 copies of values in match
labels, B9 chunking and anchored reads of stored state, B10 backoff and the timeout on Python finds.
(B6, map-gaps, is in modules/map-gaps/tests/test_reconcile.py.) Drops the database at the end."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from harness import Kernel, admin, connect, run, skip, submit  # noqa: E402

try:
    from detectors.core import (ACTIVITY, DCT_TYPE, ENTITY, FORMED, DISSOLVED, INSTRUMENT, LABEL, RDF_TYPE, USED, Match,
                                change_set_key, detector_iri, match_iri)
    from detectors.engine import Detector, Engine, Refused, changed
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")

ME, AGENT, MODULE = "kkm_detectors", "urn:kk:agent:detectors", "urn:kk:module:detectors"
SECRET = "urn:kk:scope:rev-secret"
DESC = "http://purl.org/dc/terms/description"
THING = "urn:p:Thing"
PID = os.getpid()
PLAIN, CLEARED = f"revt_plain_{PID}", f"revt_cleared_{PID}"
PLAIN_A, CLEARED_A = "urn:revt:plain", "urn:revt:cleared"
K = Kernel("rev")


def edge(s, p, o):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": o}, "evidence_type": "deterministic",
            "references": [{"source_uri": "urn:kk:test:source"}]}


def val(s, p, v):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": {"value": {"@value": v}}, "evidence_type": "deterministic",
            "references": [{"source_uri": "urn:kk:test:source"}]}


def say(ops, who=PLAIN, scope="urn:kk:scope:default"):
    agent = PLAIN_A if who == PLAIN else CLEARED_A
    return K.say(ops, scope, who, agent)


def engine(ds, **kw):
    kw.setdefault("backoff_s", 0)
    kw.setdefault("full_every", 1000)
    return Engine(ds, ME, AGENT, MODULE, **kw)


def go(e, conn):
    out = e.run_pass(conn)
    K.wait_applied()
    return out


def claim_id(subject, predicate=None):
    q = "select id from kernel.claim where subject = %s" + (" and predicate = %s" if predicate else "")
    return str(K.k.execute(q, (subject, predicate) if predicate else (subject,)).fetchone()["id"])


def own_claims(prefix):
    return K.k.execute("select subject, predicate, object_entity, object_value from kernel.claim where subject like %s "
                       "and attributed_to = %s", (prefix + "%", AGENT)).fetchall()


def edges_sql(obj, extra=""):
    return (f"select e.subject as key, array[e.subject] as nodes, array[e.claim_id] as claims from api.edges e "
            f"where e.predicate = '{RDF_TYPE}' and e.object = '{obj}' {extra} "
            f"and (%(anchors)s::text[] is null or e.subject = any(%(anchors)s::text[]))")


def main():
    K.start()
    conn = connect(ME)
    for role, agent in ((PLAIN, PLAIN_A), (CLEARED, CLEARED_A)):
        K.k.execute(f"create role {role} nologin")
        K.k.execute(f"grant kk_agent to {role}")
        K.k.execute(f"grant {role} to kk_authenticator")
    K.k.execute("set role kk_owner")
    K.k.execute("insert into kernel.agent(iri, db_role, kind, label) values (%s, %s, 'software', 'p'), (%s, %s, 'software', 'c')",
                (PLAIN_A, PLAIN, CLEARED_A, CLEARED))
    K.k.execute("insert into kernel.scope(iri, label) values (%s, 'review: secret')", (SECRET,))
    K.k.execute("reset role")
    for role, rw in ((CLEARED, True), ("kkm_validator", False)):
        K.k.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values (%s, %s, true, %s)", (SECRET, role, rw))
    check = K.check

    # ---------------------------------------------------------------- B1: events go to a scope the owner's reads may not
    say([edge("urn:e:alice", RDF_TYPE, "urn:p:Employee")])
    say([val("urn:e:alice", DESC, "SECRET-PAYLOAD")], CLEARED, SECRET)
    leak_sql = ("select e.subject || ' :: ' || (c.object_value->>'@value') as key, array[e.subject] as nodes, array[e.claim_id] as claims "
                "from api.edges e join api.claims c on c.subject = e.subject and c.predicate = '" + DESC + "' and c.status <> 'deprecated' "
                "where e.object = 'urn:p:Employee'")
    negation_sql = ("select e.subject as key, array[e.subject] as nodes, array[e.claim_id] as claims from api.edges e "
                    "where e.object = 'urn:p:Employee' and not exists (select 1 from api.claims c where c.subject = e.subject "
                    "and c.predicate = '" + DESC + "' and c.status <> 'deprecated')")
    leaky = [Detector("b1-value", CLEARED, sql=leak_sql, anchored=False), Detector("b1-negation", CLEARED, sql=negation_sql, anchored=False)]
    e1 = engine(leaky)
    out = go(e1, conn)
    check("B1 a detector whose owner reads a scope more restrictive than its event scope is refused (key and negation leaks)",
          all(out[d]["status"] == "failed" and "leak" in out[d]["error"] for d in ("b1-value", "b1-negation")), str(out))
    check("B1 and nothing of it is written: no match, no event, no copy of the secret value",
          not own_claims("urn:kk:match:b1-") and not own_claims("urn:kk:event:b1-")
          and K.k.execute("select count(*) as n from kernel.claim where object_value::text like '%%SECRET-PAYLOAD%%' and scope <> %s", (SECRET,)).fetchone()["n"] == 0)
    check("B1 validate() names the refused detectors", set(e1.validate(conn)) == {"b1-value", "b1-negation"})
    plain_ok = Detector("b1-plain", PLAIN, sql=negation_sql, anchored=False)
    check("B1 an owner cleared for nothing beyond the event scope is accepted", go(engine([plain_ok]), conn)["b1-plain"]["status"] == "ok")
    for bad in ("person_bob", "agent_x", "kk_owner", "kk_authenticator"):
        try:
            Detector("b3-owner", bad, sql="select 1")
            check(f"B3 owner {bad} is refused", False)
        except Refused:
            check(f"B3 owner {bad} is refused", True)
    try:
        engine([Detector("dup", ME, sql="select 1"), Detector("dup", ME, sql="select 2")])
        check("B2 two detectors with one name are refused", False)
    except ValueError:
        check("B2 two detectors with one name are refused", True)
    out = go(engine([Detector("super", "postgres", sql="select 1")]), conn)
    check("B3 a superuser owner is refused", out["super"]["status"] == "failed" and "superuser" in out["super"]["error"], str(out))

    # ---------------------------------------------------------------- B2: forged events and matches
    say([edge("urn:e:bob", RDF_TYPE, "urn:p:Emp")])
    prem = claim_id("urn:e:bob")
    emp = Detector("emp", ME, sql=edges_sql("urn:p:Emp"))
    det, who = detector_iri("emp"), match_iri("emp", "urn:e:bob")

    def op(s, p, o):
        return {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": o} if not isinstance(o, dict) else {"value": o},
                "evidence_type": "inferred", "derived_from": [prem]}
    say([op(who, RDF_TYPE, ENTITY), op(who, DCT_TYPE, det), op(who, LABEL, {"@value": "urn:e:bob"}),
         op("urn:rev:forged:1", RDF_TYPE, ACTIVITY), op("urn:rev:forged:1", DCT_TYPE, FORMED),
         op("urn:rev:forged:1", INSTRUMENT, det), op("urn:rev:forged:1", USED, who)])
    e2 = engine([emp])
    check("B2 a forged 'formed' event (not the detector's agent) is not taken for a stored match", e2.stored(conn, emp) == {})
    go(e2, conn)
    real = [r for r in own_claims("urn:kk:event:emp:") if r["predicate"] == DCT_TYPE]
    check("B2 so the detector still records its own formed event, and the match's own label",
          [r["object_entity"] for r in real] == [FORMED] and any(r["predicate"] == LABEL for r in own_claims(who)), str(real))
    say([op("urn:rev:forged:2", RDF_TYPE, ACTIVITY), op("urn:rev:forged:2", DCT_TYPE, DISSOLVED),
         op("urn:rev:forged:2", INSTRUMENT, det), op("urn:rev:forged:2", USED, who)])
    check("B2 a forged 'dissolved' event does not dissolve it", list(e2.stored(conn, emp)) == ["urn:e:bob"])
    say([op(match_iri("emp", "urn:e:eve"), LABEL, {"@value": "urn:e:eve"})])
    check("B2 a forged label does not make a match 'known'", e2.known(conn, emp, [Match("urn:e:eve", ("n",), ("c",))]) == set())

    # ---------------------------------------------------------------- B3: role switching in a detector's query
    esc_sql = ("select 'leak:' || (c.object_value->>'@value') as key, array['urn:e:alice'] as nodes, array[e.claim_id] as claims "
               "from (select set_config('role', '" + CLEARED + "', false)) s, api.edges e, "
               "lateral (select * from api.claims c where c.subject = 'urn:e:alice' and c.scope = '" + SECRET + "' offset 0) c "
               "where e.subject = 'urn:e:alice' and e.object = 'urn:p:Employee'")
    for owner, label in ((PLAIN, "a plain owner"), (ME, "the module's own role as owner")):
        name = "esc-" + label.split()[1]
        e3 = engine([Detector(name, owner, sql=esc_sql, anchored=False)])
        out = go(e3, conn)
        check(f"B3 {label} that switches role inside its query is stopped, nothing recorded",
              out[name]["status"] == "failed" and not own_claims(f"urn:kk:match:{name}"), str(out))
        check(f"B3 its connection does not keep the other role",
              owner not in e3.owners or e3.owners[owner].execute("select current_user").fetchone()[0] == owner)
        check(f"B3 and the module's own connection is untouched", conn.execute("select current_user").fetchone()[0] == ME)
        check(f"B3 no copy of the secret value was written", K.k.execute(
            "select count(*) as n from kernel.claim where object_value::text like '%%SECRET-PAYLOAD%%' and scope <> %s", (SECRET,)).fetchone()["n"] == 0)

    # ---------------------------------------------------------------- B4: belief and verification change sets name their claims
    say([edge("urn:e:bel", RDF_TYPE, THING)])
    cid = claim_id("urn:e:bel")
    trusted = Detector("trusted", ME, sql="select c.subject as key, array[c.subject] as nodes, array[c.id] as claims from api.claims c "
                       "where c.belief >= 0.8 and c.subject = 'urn:e:bel' and (%(anchors)s::text[] is null or c.subject = any(%(anchors)s::text[]))")
    e4 = engine([trusted])
    go(e4, conn)
    n = K.seq()
    say([{"op": "add_belief", "claim": cid, "model": "urn:kk:module:belief-corroboration-v0", "value": 0.95}])
    belief_cs = K.k.execute("select change_set from kernel.belief where claim_id = %s", (cid,)).fetchone()["change_set"]
    go(e4, conn)
    check("B4 a belief that arrives in its own change set is seen: the detector that reads belief forms", K.events(n, "trusted") == [("trusted", "formed", "urn:e:bel")], str(K.events(n, "trusted")))
    say([{"op": "add_verification", "claim": cid, "verdict": "supported", "method": "urn:kk:module:rev-verifier"}], CLEARED)
    ver_cs = K.k.execute("select change_set from kernel.verification where claim_id = %s", (cid,)).fetchone()["change_set"]
    with connect(ME) as c:
        check("B4 and the verification's change set anchors on the claim too", "urn:e:bel" in changed(c, [ver_cs]) and "urn:e:bel" in changed(c, [belief_cs]))

    # ---------------------------------------------------------------- B5: a change set the kernel refuses is not an applied event
    K.k.execute("insert into kernel.scope(iri, label) values ('urn:kk:scope:rev-vault', 'v')")
    K.k.execute("insert into kernel.scope_grant(scope, db_role, can_read, can_write) values ('urn:kk:scope:rev-vault', %s, true, true), "
                "('urn:kk:scope:rev-vault', 'kkm_validator', true, false)", (CLEARED,))
    say([edge("urn:e:carol", RDF_TYPE, "urn:p:Vaulted")], CLEARED, "urn:kk:scope:rev-vault")
    vault_claim = claim_id("urn:e:carol")
    flag = {"on": True}

    def find(c, anchors):
        return [Match("carol", ("urn:e:carol",), (vault_claim,))] if flag["on"] else []
    # the module cannot read the vault, so the kernel refuses events derived from it in the default scope (invariant 3), at apply
    inv = Detector("invalid-events", ME, find=find, anchored=True)
    e5 = engine([inv])
    r1 = go(e5, conn)["invalid-events"]
    st = K.k.execute("select status from kernel.change_set where idempotency_key like 'detectors:invalid-events|%%'").fetchall()
    check("B5 setup: the kernel invalidates the event change set after it was submitted", [s["status"] for s in st] == ["invalid"], f"{r1} {st}")
    r2 = go(e5, conn)["invalid-events"]
    check("B5 the next pass reports the refusal as a failure", r2["status"] == "failed" and "invalid" in r2["error"], str(r2))
    cur = K.k.execute("select max((info->>'cursor')::bigint) as c from kernel.run r where agent = %s and status = 'succeeded' and info->>'detector' = 'invalid-events' "
                      "and not exists (select 1 from kernel.change_set s where s.generated_by = r.id and s.status = 'invalid')", (AGENT,)).fetchone()["c"]
    check("B5 and the cursor has not moved past it: the range is looked at again", e5.cursor(conn, "invalid-events") == cur, f"{cur} {e5.cursors}")
    go(e5, conn)
    keys = [r["idempotency_key"] for r in K.k.execute("select idempotency_key from kernel.change_set where idempotency_key like 'detectors:invalid-events|%%'").fetchall()]
    check("B5 the retry is a new change set (a refused key is not reused)", len(keys) >= 2 and len(set(keys)) == len(keys) and any("|r1" in k for k in keys), str(keys))
    check("B5 each refused run is reported once", K.k.execute("select count(*) as n from kernel.run where agent = %s and info->>'detector' = 'invalid-events' and info ? 'reported'", (AGENT,)).fetchone()["n"] == K.k.execute("select count(distinct info->>'reported') as n from kernel.run where agent = %s and info ? 'reported'", (AGENT,)).fetchone()["n"] >= 1)

    # ---------------------------------------------------------------- B7: a squatted idempotency key
    say([edge("urn:e:sq", RDF_TYPE, "urn:p:Squat")])
    sq = Detector("squat", ME, sql=edges_sql("urn:p:Squat"))
    # the squatter takes the very key the detector's first pass will use (its range ends at the highest seq then)
    with connect(PLAIN) as pc, pc.cursor() as pcur:
        with run(pcur, "urn:kk:module:revt-plain") as (rid, _):
            sq_key = change_set_key("squat", None, K.seq() + 1, [Match("urn:e:sq", ("urn:e:sq",), ("c",))], [])
            submit(pcur, {"idempotency_key": sq_key, "attributed_to": PLAIN_A, "target_scope": "urn:kk:scope:default",
                          "ops": [val("urn:e:junk3", DESC, "squat")]}, rid)
    K.wait_applied()
    e7 = engine([sq])
    out = go(e7, conn)
    ev = [r for r in own_claims("urn:kk:event:squat:") if r["predicate"] == DCT_TYPE]
    check("B7 a key someone else took first does not swallow the detector's events", [r["object_entity"] for r in ev] == [FORMED] and out["squat"]["formed"] == 1, f"{out} {ev}")
    keys = [r["idempotency_key"] for r in K.k.execute("select idempotency_key from kernel.change_set where attributed_to = %s and idempotency_key like 'detectors:squat|%%'", (AGENT,)).fetchall()]
    check("B7 it used a key bound to its run", len(keys) == 1 and "|run:" in keys[0], str(keys))

    # ---------------------------------------------------------------- B8: a value in a key does not outlive its match
    state = {"on": True}
    say([val("urn:e:dave", DESC, "home address 12 Elm St")])
    dave = claim_id("urn:e:dave")

    def pii(c, anchors):
        return [Match("urn:e:dave :: 12 Elm St", ("urn:e:dave",), (dave,))] if state["on"] else []
    pd = Detector("pii", ME, find=pii, anchored=False)
    e8 = engine([pd])
    seq = []
    for on in (True, False, True, False):
        state["on"] = on
        go(e8, conn)
        lab = K.k.execute("select kernel.current_status(c.id) as st from kernel.claim c where c.subject = %s and c.predicate = %s "
                          "order by c.recorded_at", (match_iri("pii", "urn:e:dave :: 12 Elm St"), LABEL)).fetchall()
        seq.append([r["st"] for r in lab])
    check("B8 the label that carries the key is deprecated when the match dissolves",
          seq[1] == ["deprecated"] and seq[3] == ["deprecated", "deprecated"], str(seq))
    check("B8 and a match that forms again gets a current label again", seq[2] == ["deprecated", "normal"], str(seq))
    kinds = [e[1] for e in K.events(0, "pii")]       # the events join each label of the match: once per label
    check("B8 the events are formed, dissolved, formed, dissolved", [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k] == ["formed", "dissolved", "formed", "dissolved"], str(kinds))

    # ---------------------------------------------------------------- B9: chunks, and stored state read for the anchors only
    say([edge(f"urn:e:many{i}", RDF_TYPE, "urn:p:Many") for i in range(12)])
    many = Detector("many", ME, sql=edges_sql("urn:p:Many"))
    e9 = engine([many], chunk=5)
    out = go(e9, conn)
    sets = K.k.execute("select jsonb_array_length(ops) as n from kernel.change_set where attributed_to = %s and idempotency_key like 'detectors:many|%%' order by 1", (AGENT,)).fetchall()
    check("B9 twelve matches are three change sets of at most five matches each, all applied", out["many"]["formed"] == 12 and len(sets) == 3
          and all(s["n"] <= 5 * 9 for s in sets) and K.k.execute("select count(*) as n from kernel.change_set where idempotency_key like 'detectors:many|%%' and status = 'applied'").fetchone()["n"] == 3, f"{out} {sets}")
    check("B9 and they all hold", len(e9.stored(conn, many)) == 12)
    only = e9.stored(conn, many, ["urn:e:many3"], [])
    check("B9 stored state read for one anchor is the matches that name it", list(only) == ["urn:e:many3"], str(list(only)))
    check("B9 plus the keys the query found now", set(e9.stored(conn, many, ["urn:e:many3"], ["urn:e:many4"])) == {"urn:e:many3", "urn:e:many4"})
    n = K.seq()
    say([edge("urn:e:many-new", RDF_TYPE, "urn:p:Many")])
    go(e9, conn)
    check("B9 an anchored pass over twelve stored matches records only the new one", K.events(n, "many") == [("many", "formed", "urn:e:many-new")], str(K.events(n, "many")))

    # ---------------------------------------------------------------- B10: backoff, the timeout on Python finds, %%
    bad = Detector("always-broken", ME, sql="select nope from nowhere")
    e10 = engine([bad], backoff_s=60)
    go(e10, conn)
    fails = lambda: K.k.execute("select count(*) as n from kernel.run where agent = %s and info->>'detector' = 'always-broken' and status = 'failed'", (AGENT,)).fetchone()["n"]  # noqa: E731
    a = fails()
    out = [go(e10, conn)["always-broken"]["status"] for _ in range(3)]
    check("B10 a detector that failed is not tried again at once: backoff, and no failed run per pass", out == ["backoff"] * 3 and fails() == a == 1, f"{out} {a} {fails()}")
    e10.failures["always-broken"] = (1, 0.0)
    go(e10, conn)
    check("B10 and it is tried again when the wait is over", fails() == 2)

    def slow(c, anchors):
        c.execute("select pg_sleep(4)")
        return []
    t = time.time()
    out = go(engine([Detector("slow-python", ME, find=slow)], timeout_ms=500), conn)["slow-python"]
    check("B10 a Python find gets the statement timeout too", out["status"] == "failed" and "timeout" in out["error"] and time.time() - t < 3.5, f"{out} {time.time() - t:.1f}s")

    def writer(c, anchors):
        c.execute("create temp table x(a int)")
        return []
    out = go(engine([Detector("writer-python", ME, find=writer)]), conn)["writer-python"]
    check("B10 and it runs read only", out["status"] == "failed", str(out))
    like = Detector("percent", ME, sql="select e.subject as key, array[e.subject] as nodes, array[e.claim_id] as claims from api.edges e where e.subject like 'urn:e:many1%%'", anchored=False)
    out = go(engine([like]), conn)["percent"]
    check("B10 a literal % in a query is written %% (documented)", out["status"] == "ok" and out["formed"] == 3, str(out))

    K.finish("D-110 detector review checks passed")


try:
    main()
finally:
    K.stop()
    for role in (PLAIN, CLEARED):
        try:
            admin.execute(f"drop role if exists {role}")
        except Exception:
            pass

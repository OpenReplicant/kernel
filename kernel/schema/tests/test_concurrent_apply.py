#!/usr/bin/env python3
"""M6-T0 (D-109): applies from concurrent sessions keep kernel.applied in commit order and current_edge exact.

Runs against the database run.sh left (001-003 and both SQL test files applied; their roles exist), as a superuser
from PGHOST/PGUSER/PGPASSWORD, database KK_TEST_DB. Prints CONCURRENT APPLY TESTS PASSED, or exits non-zero."""
import json
import os
import sys
import threading
import time

import psycopg

DB = os.environ.get("KK_TEST_DB", "kk_test")
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"CHECK FAILED: {msg}")
    ok += 1
    print(f"ok: {msg}")


def connect(role):
    conn = psycopg.connect(dbname=DB)
    conn.execute(f"set role {role}")
    conn.commit()
    return conn


def link(ref, s, o):
    return {"op": "add_claim", "ref": ref, "subject": s, "predicate": "rdfs:seeAlso", "object": {"entity": o},
            "evidence_type": "deterministic", "references": [{"source_uri": "https://example.org/concurrent"}]}


ext = connect("t_extractor")
run = ext.execute("select api.start_run('ex:module/concurrent')").fetchone()[0]


def submit(key, ops):
    cs = ext.execute("select api.submit_change_set(%s)", (json.dumps({
        "idempotency_key": key, "attributed_to": "urn:kk:agent:extractor", "generated_by": str(run),
        "target_scope": "urn:kk:scope:default", "ops": ops}),)).fetchone()[0]
    ext.commit()
    return cs["id"]


def seq(conn, cs):
    row = conn.execute("select seq from kernel.applied where change_set = %s", (cs,)).fetchone()
    return row and row[0]


admin = psycopg.connect(dbname=DB, autocommit=True)
edges_match = "select public.edges_match()"

# 1. A applies and has not committed; B's apply waits for A's commit before it is numbered.
a_cs = submit("conc:a", [link("a", "ex:c/a", "ex:c/xa")])
b_cs = submit("conc:b", [link("b", "ex:c/b", "ex:c/xb")])
va, vb = connect("t_validator"), connect("t_validator")
check(va.execute("select api.record_validation(%s, true)", (a_cs,)).fetchone()[0] == "applied", "A applies (open)")
done = threading.Event()
result = {}


def apply_b():
    result["b"] = vb.execute("select api.record_validation(%s, true)", (b_cs,)).fetchone()[0]
    done.set()


t = threading.Thread(target=apply_b)
t.start()
time.sleep(1.0)
check(not done.is_set(), "B's apply waits while A's is uncommitted")
check(seq(admin, a_cs) is None, "a reader does not see A's number before A commits")
va.commit()
t.join(10)
check(done.is_set() and result.get("b") == "applied", "B applies once A commits")
check(seq(admin, b_cs) is None, "B's number is not visible before B commits")
vb.commit()
check(seq(admin, a_cs) < seq(admin, b_cs), "numbers follow commit order")
check(admin.execute(edges_match).fetchone()[0], "edges match after the two applies")

# 2. Two status changes to the same claim from two sessions: the edge ends where current_status says.
claim = admin.execute("select id from kernel.claim where change_set = %s", (a_cs,)).fetchone()[0]
p_cs = submit("conc:p", [{"op": "set_status", "claim": str(claim), "status": "preferred", "reason": "review"}])
d_cs = submit("conc:d", [{"op": "set_status", "claim": str(claim), "status": "deprecated", "reason": "retracted"}])
check(va.execute("select api.record_validation(%s, true)", (p_cs,)).fetchone()[0] == "applied", "preferred applies (open)")
t = threading.Thread(target=lambda: result.update(d=vb.execute("select api.record_validation(%s, true)", (d_cs,)).fetchone()[0]))
t.start()
time.sleep(0.5)
va.commit()
t.join(10)
vb.commit()
check(result.get("d") == "applied", "the second status change applies")
check(admin.execute(edges_match).fetchone()[0], "the edge agrees with the claim's status after racing changes")

# 3. Many applies from four sessions at once: one number each, distinct, and the edges exact.
keys = [f"conc:many:{i}" for i in range(40)]
ids = [submit(k, [link("m", f"ex:c/m{i}", f"ex:c/m{(i * 7) % 40}")]) for i, k in enumerate(keys)]
errors = []


def worker(chunk):
    conn = connect("t_validator")
    try:
        for cs in chunk:
            conn.execute("select api.record_validation(%s, true)", (cs,))
            conn.commit()
    except Exception as e:  # noqa: BLE001  reported below
        errors.append(repr(e))
    finally:
        conn.close()


threads = [threading.Thread(target=worker, args=(ids[i::4],)) for i in range(4)]
for th in threads:
    th.start()
for th in threads:
    th.join(60)
check(not errors, f"forty applies from four sessions raise nothing {errors[:1]}")
nums = [seq(admin, cs) for cs in ids]
check(all(nums) and len(set(nums)) == 40, "each has one distinct number")
check(admin.execute("select count(*) from kernel.applied").fetchone()[0]
      == admin.execute("select count(*) from kernel.change_set where status = 'applied'").fetchone()[0],
      "one number per applied change set")
check(admin.execute(edges_match).fetchone()[0], "edges match after the concurrent applies")

# 4. Opposite-order status changes from two sessions (the reviewer's deadlock): none is lost.
pairs = []
for i in range(20):
    x = submit(f"conc:x{i}", [link("x", f"ex:c/x{i}", "ex:c/xo")])
    y = submit(f"conc:y{i}", [link("y", f"ex:c/y{i}", "ex:c/yo")])
    pairs.append((x, y))
va.commit(); vb.commit()
for x, y in pairs:
    for cs in (x, y):
        va.execute("select api.record_validation(%s, true)", (cs,))
        va.commit()
races = []
for i, (x, y) in enumerate(pairs):
    cx = str(admin.execute("select id from kernel.claim where change_set = %s", (x,)).fetchone()[0])
    cy = str(admin.execute("select id from kernel.claim where change_set = %s", (y,)).fetchone()[0])
    one = submit(f"conc:xy{i}", [{"op": "set_status", "claim": cx, "status": "preferred", "reason": "review"},
                                 {"op": "set_status", "claim": cy, "status": "preferred", "reason": "review"}])
    two = submit(f"conc:yx{i}", [{"op": "set_status", "claim": cy, "status": "normal", "reason": "review"},
                                 {"op": "set_status", "claim": cx, "status": "normal", "reason": "review"}])
    races.append((one, two))
outcomes = []


def validate(cs):
    conn = connect("t_validator")
    try:
        outcomes.append(conn.execute("select api.record_validation(%s, true)", (cs,)).fetchone()[0])
        conn.commit()
    finally:
        conn.close()


for one, two in races:
    ts = [threading.Thread(target=validate, args=(cs,)) for cs in (one, two)]
    for th in ts:
        th.start()
    for th in ts:
        th.join(30)
check(outcomes.count("applied") == 40, f"forty opposite-order status changes all apply ({sorted(set(outcomes))})")
check(admin.execute(edges_match).fetchone()[0], "edges match after the opposite-order changes")

# 5. A transaction that applies two change sets, against a concurrent apply: neither is lost.
m1 = submit("conc:multi1", [link("m", "ex:c/multi1", "ex:c/mo")])
m2 = submit("conc:multi2", [link("m", "ex:c/multi2", "ex:c/mo")])
m3 = submit("conc:multi3", [link("m", "ex:c/multi3", "ex:c/mo3")])
check(va.execute("select api.record_validation(%s, true)", (m1,)).fetchone()[0] == "applied", "first of two in one transaction")
t = threading.Thread(target=validate, args=(m3,))
outcomes.clear()
t.start()
time.sleep(0.5)
check(va.execute("select api.record_validation(%s, true)", (m2,)).fetchone()[0] == "applied", "second in the same transaction")
va.commit()
t.join(30)
check(outcomes == ["applied"], "the concurrent apply waits and applies")

# 6. Only the kernel can take the lock; an advisory lock on the old key stalls nothing.
plain = connect("t_verifier")
try:
    plain.execute("lock table kernel.applied in share row exclusive mode")
    check(False, "an agent cannot lock kernel.applied")
except psycopg.errors.InsufficientPrivilege:
    plain.rollback()
    check(True, "an agent cannot lock kernel.applied")
try:
    plain.execute("select kernel.serialize()")
    check(False, "an agent cannot call kernel.serialize")
except psycopg.errors.InsufficientPrivilege:
    plain.rollback()
    check(True, "an agent cannot call kernel.serialize")
plain.execute("select pg_advisory_xact_lock(hashtext('kernel.applied'))")
a6 = submit("conc:adv", [link("v", "ex:c/adv", "ex:c/advo")])
check(va.execute("select api.record_validation(%s, true)", (a6,)).fetchone()[0] == "applied", "an agent's advisory lock does not stall applies")
va.commit()
plain.rollback()

# 7. An apply whose transaction rolls back leaves no edge and no number.
rb = submit("conc:rollback", [link("r", "ex:c/rollback", "ex:c/rbo")])
check(va.execute("select api.record_validation(%s, true)", (rb,)).fetchone()[0] == "applied", "applied inside an open transaction")
va.rollback()
check(seq(admin, rb) is None, "rolled back: no number")
check(not admin.execute("select 1 from kernel.current_edge where subject like '%%rollback'").fetchone(), "rolled back: no edge")

# 8. A reader polling `seq > cursor` while four sessions apply sees every apply once, in rising order.
ids = [submit(f"conc:poll:{i}", [link("p", f"ex:c/p{i}", "ex:c/po")]) for i in range(40)]
seen, stop = [], threading.Event()


def poll():
    conn = psycopg.connect(dbname=DB, autocommit=True)
    cursor = admin.execute("select coalesce(max(seq), 0) from kernel.applied").fetchone()[0]
    while not stop.is_set() or len(seen) < 40:
        rows = conn.execute("select seq, change_set from kernel.applied where seq > %s order by seq", (cursor,)).fetchall()
        for n, cs in rows:
            seen.append((n, cs))
            cursor = n
        if stop.is_set() and not rows:
            break
        time.sleep(0.01)
    conn.close()


reader = threading.Thread(target=poll)
reader.start()
threads = [threading.Thread(target=worker, args=(ids[i::4],)) for i in range(4)]
for th in threads:
    th.start()
for th in threads:
    th.join(60)
stop.set()
reader.join(30)
check([n for n, _ in seen] == sorted(n for n, _ in seen) and len({cs for _, cs in seen}) == len(seen),
      "the polling reader saw rising numbers, each once")
check({str(cs) for _, cs in seen} >= {str(cs) for cs in ids}, "and every apply")

print(f"CONCURRENT APPLY TESTS PASSED ({ok})")

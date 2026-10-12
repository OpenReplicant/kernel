"""The S1 gate (docs/PLAN.md): a test module loads; a claim written through it carries
provenance and time; a superseded claim is still answerable "as of" a past time; a write loop
is stopped by the guard."""
import re
import shutil
import time

import psycopg
import pytest
import requests
from psycopg.types.json import Jsonb

from conftest import NOTES, wait_for
from kernel import claims, db, modules

TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")


def note_id(conn, key):
    row = conn.execute("MATCH (n:note) WHERE n.key = %s RETURN id(n)::text", (Jsonb(key),)).fetchone()
    conn.commit()
    return row[0] if row else None


# --- a test module loads ---------------------------------------------------------------------

def test_module_loads(notes, conn, rulego):
    assert notes["module"] == "notes" and notes["version"] == "0.1.0"
    assert set(notes["labels"]) == {"note", "links_to"}
    [mod] = [m for m in modules.installed(conn) if m["name"] == "notes"]
    assert mod["chains"] == ["notes_set_status", "notes_echo", "notes_similar", "notes_read"]
    assert mod["activations"][0]["chain"] == "notes_echo"
    assert TIME.match(mod["recorded_at"])
    assert claims.find_agent(conn, "module:notes")
    # the labels carry the kernel's triggers and inherit from thing
    trig = {r[0] for r in conn.execute(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass", (f"{db.graph_name()}.note",))}
    assert {"k_guard", "k_outbox"} <= trig
    # chains are deployed with the module's vars
    for cid in notes["chains"]:
        r = requests.get(f"{rulego.url}/api/v1/rules/{cid}", timeout=5)
        assert r.ok and r.json()["ruleChain"]["configuration"]["vars"]["module"] == "notes", cid
    # installing again changes nothing
    assert modules.install(NOTES, conn, rulego)["id"] == notes["id"]


def test_changed_manifest_needs_a_new_version(notes, conn, rulego, tmp_path):
    copy = tmp_path / "notes"
    shutil.copytree(NOTES, copy)
    manifest = copy / "module.yaml"
    manifest.write_text(manifest.read_text().replace("- {name: notes.ping, as: claim}",
                                                     "- {name: notes.ping, as: claim}\n    - {name: notes.tag, as: claim}"))
    with pytest.raises(modules.ManifestError, match="bump the version"):
        modules.install(copy, conn, rulego)
    conn.rollback()
    manifest.write_text(manifest.read_text().replace('version: "0.1.0"', 'version: "0.2.0"'))
    new = modules.install(copy, conn, rulego)
    [held] = [m for m in modules.installed(conn) if m["name"] == "notes"]
    assert held["version"] == "0.2.0" and held["id"] == new["id"]
    old = conn.execute("MATCH (m:module) WHERE id(m) = %s::graphid RETURN m.status, m.retired_at",
                       (notes["id"],)).fetchone()
    assert old[0] == "superseded" and TIME.match(old[1])
    # back to the original for the other tests: 0.1.0 is installed again as a new record
    modules.install(NOTES, conn, rulego)
    assert [m["version"] for m in modules.installed(conn) if m["name"] == "notes"] == ["0.1.0"]


# --- a claim written through it carries provenance and time ------------------------------------

def test_claim_through_module_has_provenance_and_time(notes, conn, rulego):
    before = claims.now(conn)
    out = rulego.execute("notes_set_status", {"note": "alpha", "status": "draft"})
    [cid] = out["written"]["claims"]
    w = claims.why(conn, cid)
    c = w["claim"]
    assert (c["predicate"], c["value"], c["method"], c["confidence"]) == ("notes.status", "draft", "stated", 0.9)
    assert w["span"] == "$.status" and c["span"] == "$.status"
    assert w["source"]["kind"] == "command"
    assert w["source"]["content"] == {"note": "alpha", "status": "draft"}
    assert w["source"]["sha256"] == claims.content_hash({"note": "alpha", "status": "draft"})
    assert w["agent"]["name"] == "module:notes" and w["agent"]["version"] == "0.1.0"
    assert TIME.match(c["recorded_at"]) and before <= c["recorded_at"] <= claims.now(conn)
    assert c["depth"] == 0 and "causation" not in c          # on command: no causing change
    assert w["subject"] == note_id(conn, "alpha")


# --- a superseded claim is still answerable "as of" a past time ---------------------------------

def test_superseded_claim_answerable_as_of(notes, conn, rulego):
    first = rulego.execute("notes_set_status", {"note": "beta", "status": "draft"})["written"]["claims"][0]
    t1 = claims.now(conn)
    conn.commit()
    time.sleep(0.01)
    second = rulego.execute("notes_set_status", {"note": "beta", "status": "final"})["written"]["claims"][0]
    beta = note_id(conn, "beta")

    now = claims.held(conn, beta, "notes.status")
    assert [(h["id"], h["value"]) for h in now] == [(second, "final")]
    then = claims.held(conn, beta, "notes.status", known_at=t1)
    assert [(h["id"], h["value"]) for h in then] == [(first, "draft")]
    # the old claim is unchanged but retired, and linked from its replacement
    old = claims.why(conn, first)
    assert old["claim"]["status"] == "superseded" and old["claim"]["retired_at"] > t1
    assert old["claim"]["value"] == "draft" and old["superseded_by"] == second
    assert claims.why(conn, second)["supersedes"] == first
    # before either was recorded, nothing was held
    assert claims.held(conn, beta, "notes.status", known_at="2000-01-01T00:00:00.000000Z") == []


# --- a write loop is stopped by the guard ----------------------------------------------------

def test_write_loop_stopped_by_guard(notes, conn, bridge, limits):
    limits(max_depth=3)
    bridge()
    with db.activation(conn):
        src = claims.ensure_source(conn, "command", {"test": "ping", "at": claims.now(conn)})
        me = claims.ensure_agent(conn, "test-runner", kind="person")
        gamma = claims.ensure_thing(conn, "note", "gamma")
        claims.assert_claim(conn, gamma, "notes.ping", 0, method="stated", confidence=1.0,
                            source=src, agent=me, span="$.test")

    def stopped():
        r = conn.execute("""SELECT seq, depth, last_error FROM kernel.outbox
                            WHERE status = 'stopped' AND label = 'claim'""").fetchall()
        conn.commit()
        return r

    [(seq, depth, error)] = wait_for(stopped, timeout=30)
    assert depth == 3 and "kernel loop guard: depth 4 exceeds max_depth 3" in error

    pings = claims.held(conn, gamma, "notes.ping")
    assert sorted((p["value"], p["depth"]) for p in pings) == [(0, 0), (1, 1), (2, 2), (3, 3)]
    # each reasoner write cites the change that caused it
    for p in pings[1:]:
        assert p["causation"].startswith("outbox:") and p["method"] == "computed"
        assert claims.why(conn, p["id"])["source"]["kind"] == "change"
    # nothing left pending: the loop is over, not retrying
    def pending():
        n = conn.execute("SELECT count(*) FROM kernel.outbox WHERE status = 'pending'").fetchone()[0]
        conn.commit()
        return n
    assert wait_for(lambda: pending() == 0, timeout=5)


def test_fanout_guard(notes, conn, limits):
    limits(max_fanout=5)
    with pytest.raises(Exception, match="kernel loop guard: 6 writes in one transaction exceed max_fanout 5"):
        with db.activation(conn):
            for i in range(6):
                claims.ensure_thing(conn, "note", f"fan-{i}")
    assert conn.execute("MATCH (n:note) WHERE n.key = 'fan-0' RETURN count(n)").fetchone()[0] == 0


def test_guard_cannot_be_switched_off_by_a_missing_limit(notes, conn):
    with conn.transaction():
        conn.execute("DELETE FROM kernel.config WHERE key = 'max_depth'")
        with pytest.raises(Exception, match="limit max_depth missing"):
            with conn.transaction():  # savepoint
                claims.ensure_thing(conn, "note", "unguarded")
        raise psycopg.Rollback()  # put the limit back
    assert conn.execute("SELECT value FROM kernel.config WHERE key = 'max_depth'").fetchone()[0] == 8

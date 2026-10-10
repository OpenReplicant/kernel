"""M2 gate: kernel core, on a toy domain unrelated to agent design, with evidence that
isn't a paper. A thermostat loop (sensor -> controller -> heater, coupled through ports),
a sensor log recorded by an instrument (observed) and a technician's report (stated)."""
import uuid
from datetime import datetime, timezone

from pathlib import Path

import psycopg
import pytest

from kernel import EvidenceRequired, Kernel, KernelError


@pytest.fixture
def k(db_url, tmp_path):
    kern = Kernel.connect(db_url, data_root=tmp_path / "data")
    yield kern
    kern.conn.close()


@pytest.fixture
def w(k, tmp_path):
    """The toy world: a unique namespace per test, its predicates, agents and sources."""
    ns = f"urn:test:{uuid.uuid4().hex[:8]}:"
    n = lambda name: ns + name                                          # noqa: E731
    k.load_vocabulary(Path(__file__).parent / "thermostat.yaml")
    pred = {p: k.node_id(f"th:{p}") for p in ("temperature", "setpoint", "status")}
    me = k.ensure_node("agent", n("ingest-script"), "ingest script v0")
    sensor = k.ensure_node("agent", n("sensor-7"), "thermometer #7")
    tech = k.ensure_node("agent", n("technician"), "Dana, technician")
    room = k.ensure_node("thing", n("room"), "Room 101")
    world = k.create_context("world", "world", iri=n("ctx/world"))

    log = tmp_path / "sensor.log"
    log.write_text("09:00 21.0C\n09:05 21.4C\n09:10 22.1C\n" + f"# {ns}\n")   # unique per test
    report = tmp_path / "report.txt"
    report.write_text("Visited room 101. Thermostat setpoint is 21C. Heater works.\n" + f"# {ns}\n")
    log_sha = k.put_source(log, "text/plain")
    rep_sha = k.put_source(report, "text/plain")
    k.assert_(k.source_node(log_sha), "k:produced_by", sensor, context=world, method="defaulted",
              confidence=1, asserted_by=me, args={"as": "instrument"})
    k.assert_(k.source_node(rep_sha), "k:produced_by", tech, context=world, method="defaulted",
              confidence=1, asserted_by=me, args={"as": "author"})

    class W:
        pass
    o = W()
    o.__dict__.update(ns=ns, n=n, pred=pred, me=me, sensor=sensor, tech=tech, room=room,
                      world=world, log_sha=log_sha, rep_sha=rep_sha)
    return o


def test_source_is_content_addressed_read_only_and_idempotent(k, w, tmp_path):
    copy = tmp_path / "again.log"
    copy.write_text((tmp_path / "sensor.log").read_text())
    assert k.put_source(copy, "text/plain") == w.log_sha
    path, = k.conn.execute("select path from kb.source where sha256 = %s", (w.log_sha,)).fetchone()
    stored = k.data_root / path
    assert path.startswith(f"sources/sha256/{w.log_sha[:2]}/{w.log_sha[2:4]}/")
    assert stored.read_text().startswith("09:00") and not stored.stat().st_mode & 0o222
    s1 = k.add_span(w.log_sha, {"path": "sensor.log", "line_start": 3, "line_end": 3})
    assert k.add_span(w.log_sha, {"line_end": 3, "line_start": 3, "path": "sensor.log"}) == s1


def test_each_method(k, w):
    reading = k.add_span(w.log_sha, {"line_start": 3, "line_end": 3}, "09:10 22.1C")
    said = k.add_span(w.rep_sha, {"char_start": 18, "char_end": 46}, "Thermostat setpoint is 21C")
    t = datetime(2026, 10, 10, 9, 10, tzinfo=timezone.utc)
    obs = k.assert_(w.room, w.pred["temperature"], value=22.1, context=w.world, method="observed",
                    confidence=0.95, evidence=[reading], valid_from=t, asserted_by=w.me)
    st = k.assert_(w.room, w.pred["setpoint"], value=21, context=w.world, method="stated",
                   confidence=0.9, evidence=[said], asserted_by=w.me)
    inf = k.assert_(w.room, w.pred["status"], value="overshooting", context=w.world, method="inferred",
                    confidence=0.6, asserted_by=w.me)
    comp = k.assert_(w.room, w.pred["status"], value={"error_c": 1.1}, context=w.world,
                     method="computed", confidence=1, asserted_by=w.me)
    k.link(comp, obs, "derived_from")
    k.link(comp, st, "derived_from")
    dflt = k.assert_(w.room, w.pred["setpoint"], value=20, context=w.world, method="defaulted",
                     confidence=0.3, asserted_by=w.me)
    got = {a.id: a.method for a in k.query(subject=w.room, context=w.world)}
    assert got == {obs: "observed", st: "stated", inf: "inferred", comp: "computed", dflt: "defaulted"}


@pytest.mark.parametrize("method", ["stated", "observed"])
def test_evidence_required(k, w, method):
    with pytest.raises(EvidenceRequired):
        k.assert_(w.room, w.pred["setpoint"], value=21, context=w.world, method=method,
                  confidence=1, asserted_by=w.me)
    # The database refuses too, for anyone bypassing the library.
    with pytest.raises(psycopg.errors.RaiseException, match="no evidence span"):
        k.conn.execute(
            "insert into kb.assertion (id, subject, predicate, value, context, method, confidence, asserted_by)"
            " values (%s, %s, %s, '21', %s, %s, 1, %s)",
            (uuid.uuid4(), w.room, w.pred["setpoint"], w.world, method, w.me))
    # Staged is allowed; promoting without evidence is not.
    staged = k.assert_(w.room, w.pred["setpoint"], value=21, context=w.world, method=method,
                       confidence=1, asserted_by=w.me, status="staged")
    with pytest.raises(psycopg.errors.RaiseException, match="no evidence span"):
        k.conn.execute("update kb.assertion set status = 'accepted' where id = %s", (staged,))


def test_assertions_cannot_be_edited_or_deleted(k, w):
    a = k.assert_(w.room, w.pred["status"], value="ok", context=w.world, method="inferred",
                  confidence=0.5, asserted_by=w.me)
    with pytest.raises(psycopg.errors.RaiseException, match="only status may change"):
        k.conn.execute("update kb.assertion set value = '\"broken\"' where id = %s", (a,))
    with pytest.raises(psycopg.errors.RaiseException, match="never deleted"):
        k.conn.execute("delete from kb.assertion where id = %s", (a,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        k.conn.execute("delete from kb.source where sha256 = %s", (w.log_sha,))


def test_supersede_and_known_at(k, w):
    old = k.assert_(w.room, w.pred["setpoint"], value=21, context=w.world, method="inferred",
                    confidence=0.7, asserted_by=w.me, args={"unit": "C"})
    before, = k.conn.execute("select clock_timestamp()").fetchone()
    new = k.supersede(old, value=22, asserted_by=w.tech, reason="setpoint changed")
    then = k.query(subject=w.room, predicate=w.pred["setpoint"], context=w.world, known_at=before)
    now = k.query(subject=w.room, predicate=w.pred["setpoint"], context=w.world)
    assert [(a.id, a.value) for a in then] == [(old, 21)]
    assert [(a.id, a.value, a.args) for a in now] == [(new, 22, {"unit": "C"})]
    assert k.get(old).status == "superseded"
    tree = k.why(new)
    assert tree["asserted_by"] == w.n("technician")
    sup = [l for l in tree["links"] if l["kind"] == "supersedes"]
    assert sup and sup[0]["why"]["value"] == 21


def test_retract_records_who_and_why(k, w):
    a = k.assert_(w.room, w.pred["status"], value="heater broken", context=w.world, method="inferred",
                  confidence=0.4, asserted_by=w.me)
    k.retract(a, "heater was fine", by=w.tech)
    assert k.query(subject=w.room, predicate=w.pred["status"], context=w.world) == []
    last = k.why(a)["status_history"][-1]
    assert last == {**last, "status": "retracted", "by": w.n("technician"), "reason": "heater was fine"}


def test_contradictions_live_in_separate_perspectives(k, w):
    tech_view = k.create_context("perspective", "per technician", parent=w.world)
    sensor_view = k.create_context("perspective", "per sensor log", parent=w.world)
    k.assert_(w.tech, "k:holds", tech_view, context=w.world, method="defaulted", confidence=1,
              asserted_by=w.me)
    said = k.add_span(w.rep_sha, {"char_start": 47, "char_end": 60}, "Heater works.")
    shown = k.add_span(w.log_sha, {"line_start": 1, "line_end": 3})
    a = k.assert_(w.room, w.pred["status"], value="heater works", context=tech_view, method="stated",
                  confidence=0.8, evidence=[said], asserted_by=w.me)
    b = k.assert_(w.room, w.pred["status"], value="heater overshoots", context=sensor_view,
                  method="inferred", confidence=0.6, evidence=[shown], asserted_by=w.me)
    k.link(b, a, "contradicts")
    assert [x.id for x in k.query(subject=w.room, predicate=w.pred["status"], context=tech_view)] == [a]
    assert [x.id for x in k.query(subject=w.room, predicate=w.pred["status"], context=sensor_view)] == [b]
    both = k.query(subject=w.room, predicate=w.pred["status"], context=w.world)
    assert {x.id for x in both} == {a, b}                                # sub-contexts included
    assert k.query(subject=w.room, predicate=w.pred["status"], context=w.world,
                   include_subcontexts=False) == []


def test_why_reaches_span_source_and_instrument(k, w):
    span = k.add_span(w.log_sha, {"line_start": 3, "line_end": 3}, "09:10 22.1C")
    a = k.assert_(w.room, w.pred["temperature"], value=22.1, context=w.world, method="observed",
                  confidence=0.95, evidence=[span], asserted_by=w.me)
    tree = k.why(a)
    assert tree["method"] == "observed" and tree["asserted_by"] == w.n("ingest-script")
    ev, = tree["evidence"]
    assert ev["locator"] == {"line_start": 3, "line_end": 3} and ev["excerpt"] == "09:10 22.1C"
    assert ev["source"]["path"].endswith(w.log_sha + ".log")
    assert ev["source"]["produced_by"] == [{"agent": w.n("sensor-7"), "as": "instrument", "method": "defaulted"}]


def test_valid_at(k, w):
    t9 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    t10 = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    k.assert_(w.room, w.pred["setpoint"], value=20, context=w.world, method="inferred", confidence=1,
              valid_from=t9, valid_to=t10, asserted_by=w.me)
    k.assert_(w.room, w.pred["setpoint"], value=23, context=w.world, method="inferred", confidence=1,
              valid_from=t10, asserted_by=w.me)
    at = lambda t: [a.value for a in k.query(subject=w.room, predicate=w.pred["setpoint"],  # noqa: E731
                                             context=w.world, valid_at=t)]
    assert at(datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc)) == [20]
    assert at(datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)) == [23]


def test_bindings_and_couplings_round_trip(k, w):
    loop = k.create_context("system", "thermostat loop")
    roles = {r: k.ensure_node("role", w.n(f"loop/{r}")) for r in ("sensing", "control", "actuation")}
    # The thermometer is an agent (it produced the log) and also a thing playing a role.
    things = {"sensor-7": w.sensor, **{t: k.ensure_node("thing", w.n(t)) for t in ("pid-1", "heater-2")}}
    for r, t in zip(roles, things):
        k.assert_(things[t], "k:plays", roles[r], context=loop, method="inferred", confidence=1,
                  asserted_by=w.me)
    ports = {}
    for r, names in (("sensing", ["reading"]), ("control", ["input", "command"]), ("actuation", ["power"])):
        for p in names:
            ports[p] = k.ensure_node("port", w.n(f"loop/{r}#{p}"))
            k.assert_(roles[r], "k:has_port", ports[p], context=loop, method="inferred", confidence=1,
                      asserted_by=w.me)
    k.assert_(ports["reading"], "k:couples", ports["input"], context=loop, method="inferred",
              confidence=1, asserted_by=w.me, args={"kind": "stream"})
    k.assert_(ports["command"], "k:couples", ports["power"], context=loop, method="inferred",
              confidence=1, asserted_by=w.me, args={"kind": "call"})
    k.assert_(things["heater-2"], "k:depends_on", things["pid-1"], context=loop, method="inferred",
              confidence=1, asserted_by=w.me, args={"kind": "control"})

    assert k.bindings(loop) == {w.n("loop/sensing"): [w.n("sensor-7")],
                                w.n("loop/control"): [w.n("pid-1")],
                                w.n("loop/actuation"): [w.n("heater-2")]}
    got = sorted((c["from"], c["from_role"], c["to"], c["to_role"], c["kind"]) for c in k.couplings(loop))
    assert got == sorted([
        (w.n("loop/sensing#reading"), w.n("loop/sensing"), w.n("loop/control#input"), w.n("loop/control"), "stream"),
        (w.n("loop/control#command"), w.n("loop/control"), w.n("loop/actuation#power"), w.n("loop/actuation"), "call"),
    ])


def test_reference_errors(k, w):
    with pytest.raises(KernelError, match="unknown prefix"):
        k.node_id("zz:nothing")
    with pytest.raises(KernelError, match="expected predicate"):
        k.assert_(w.room, w.room, value=1, context=w.world, method="inferred", confidence=1, asserted_by=w.me)
    with pytest.raises(KernelError, match="already exists as a thing"):
        k.ensure_node("agent", w.n("room"))

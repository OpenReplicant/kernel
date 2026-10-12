"""The S2 gate (docs/PLAN.md): an action changes because of recalled state, and it can be traced
back through claims to the observations behind it.

One entity, one module of each type (modules/climate*): a perceiver on a live stream (the room
simulator, world/climate_sim.py), an ontology, a reasoner on change (comfort), a scheduled poll
(the control tick), a recaller and an effector.
"""
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from conftest import wait_for
from kernel import claims, modules
from kernel.rulego import ChainFailed

ROOT = Path(__file__).resolve().parent.parent
SIM = "http://127.0.0.1:9191"


@pytest.fixture(scope="module")
def climate(fresh_core, rulego):
    from kernel import db
    with db.connect() as c:
        installed = [modules.install(ROOT / "modules" / m, c, rulego)
                     for m in ("climate_sense", "climate_comfort", "climate_control")]
    yield installed
    # stop the schedule: the tick chain would keep running against the next test session's graph
    requests.delete(f"{rulego.url}/api/v1/rules/climate_control_tick", timeout=5)


@pytest.fixture(scope="module")
def world():
    sim = subprocess.Popen([sys.executable, str(ROOT / "world" / "climate_sim.py"), "--rooms", "lounge=19",
                            "--tick", "0.3", "--port", "9191"], stdout=subprocess.DEVNULL)
    assert wait_for(lambda: _up(), timeout=10)
    yield
    sim.terminate()
    sim.wait(5)


def _up():
    try:
        return requests.get(f"{SIM}/state", timeout=1).ok
    except requests.RequestException:
        return False


def sim_switches():
    return requests.get(f"{SIM}/state", timeout=2).json()["switches"]


def room_claims(conn, predicate):
    """All claims (held or retired) of a predicate about the lounge, oldest first."""
    rows = conn.execute("""MATCH (c:claim)-[:about]->(r:room) WHERE r.key = 'lounge' AND c.predicate = %s
                           RETURN id(c)::text ORDER BY c.recorded_at""", (claims.Jsonb(predicate),)).fetchall()
    conn.commit()
    return [r[0] for r in rows]


def test_modules_of_each_type(climate, conn):
    types = {t for m in modules.installed(conn) if m["name"].startswith("climate") for t in m["types"]}
    assert types == {"ontology", "perceiver", "reasoner", "recaller", "effector"}


def test_action_changes_because_of_recalled_state(climate, world, conn, bridge, rulego):
    bridge()
    rulego.execute("climate_sense_setpoint", {"room": "lounge", "celsius": 21, "by": "gate-test"})

    # the world: the heater goes on (room at 19 is cold), then off (it got warm)
    assert wait_for(lambda: [s["heater"] for s in sim_switches()][:2] == [True, False], timeout=60), \
        "the heater did not go on and then off"

    # the graph: two decisions, on then off, each derived from the comfort belief it recalled
    decisions = [claims.trace(conn, d, depth=3) for d in room_claims(conn, "climate.decision")[:2]]
    assert [d["value"] for d in decisions] == [{"heater": True}, {"heater": False}]
    comforts = []
    for d in decisions:
        assert d["method"] == "inferred" and d["agent"] == "module:climate_control"
        assert d["source"]["kind"] == "recall"  # the recalled context it decided on
        [comfort] = [x for x in d["derived_from"] if x["predicate"] == "climate.comfort"]
        assert d["source"]["content"]["state"]["comfort"]["id"] == comfort["id"]  # recalled, then used
        comforts.append(comfort["value"])
    assert comforts == ["cold", "warm"]  # the action changed because the recalled belief changed

    # ...and back through the belief to the observations behind it
    off = decisions[1]
    [comfort] = [x for x in off["derived_from"] if x["predicate"] == "climate.comfort"]
    assert comfort["agent"] == "module:climate_comfort" and comfort["source"]["kind"] == "change"
    by_pred = {x["predicate"]: x for x in comfort["derived_from"]}
    temp, setp = by_pred["climate.temperature"], by_pred["climate.setpoint"]
    assert temp["method"] == "observed" and temp["source"]["kind"] == "reading" and temp["span"] == "$.celsius"
    assert temp["source"]["content"]["celsius"] == temp["value"] > 21.5  # the raw reading, unchanged
    assert setp["method"] == "stated" and setp["source"]["content"] == {"room": "lounge", "celsius": 21, "by": "gate-test"}


def test_actions_are_recorded_and_come_back_as_observations(climate, world, conn):
    # (runs after the gate test, on the graph and world it left; the loop is still running).
    # The effector records a command after the world answers, so wait for the graph to catch up.
    switches = sim_switches()
    assert wait_for(lambda: len(room_claims(conn, "climate.heater_command")) >= len(switches), timeout=10)
    commands = [claims.trace(conn, c, depth=1) for c in room_claims(conn, "climate.heater_command")]
    decisions = room_claims(conn, "climate.decision")
    assert len(commands) >= 2
    for cmd in commands:
        assert cmd["method"] == "observed" and cmd["source"]["kind"] == "effect"
        effect = cmd["source"]["content"]
        assert effect["response"]["heater"] == effect["request"]["on"] == cmd["value"]
        assert [d["id"] for d in cmd["derived_from"]] == [effect["decision"]] and effect["decision"] in decisions
    # the world got exactly the commands the graph records (more may have happened since the read)
    command_times = [c["source"]["content"]["response"]["at"] for c in commands]
    assert command_times[:len(switches)] == [w["at"] for w in switches]
    # the first command's effect was perceived: a reading taken after the world switched the heater
    # on shows it on (world time: the reading can be recorded before the command's own record)
    switched_at = commands[0]["source"]["content"]["response"]["at"]
    seen = [claims.why(conn, h)["claim"] for h in room_claims(conn, "climate.heater")]
    assert any(h["value"] is True and h["valid_from"] >= switched_at for h in seen)


def test_effector_acts_only_on_a_held_decision(climate, rulego, world):
    with pytest.raises(ChainFailed, match="refusing to act"):
        rulego.execute("climate_heater_switch", {"room": "lounge", "on": True, "decision": "4.999999"})


def test_policy():
    sys.path.insert(0, str(ROOT / "modules" / "climate_control" / "steps"))
    sys.path.insert(0, str(ROOT / "modules" / "climate_comfort" / "steps"))
    from comfort import judge
    from decide import decide
    assert [judge(t, 21, 0.5) for t in (20.4, 20.5, 21.5, 21.6)] == ["cold", "ok", "ok", "warm"]
    obs = lambda v, t: {"id": "1", "value": v, "recorded_at": t}
    assert decide({"comfort": obs("cold", "a")}) == (True, False)
    assert decide({"comfort": obs("warm", "a"), "heater": obs(True, "b")}) == (False, True)
    assert decide({"comfort": obs("ok", "a"), "heater": obs(True, "b")}) == (None, True)
    # a decision newer than the last observation counts as the current state
    assert decide({"comfort": obs("cold", "a"), "heater": obs(False, "b"),
                   "decision": obs({"heater": True}, "c")}) == (True, True)

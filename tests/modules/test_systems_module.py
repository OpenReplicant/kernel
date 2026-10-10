"""M3 gate: a non-agent system validates with only the systems module loaded."""
import uuid
from pathlib import Path

import pytest

from kernel import Kernel

LOOP_VOCAB = Path(__file__).parent / "control_loop.yaml"


@pytest.fixture
def k(db_url, tmp_path):
    kern = Kernel.connect(db_url, data_root=tmp_path)
    kern.load_module("systems")
    kern.load_vocabulary(LOOP_VOCAB)
    yield kern
    kern.conn.close()


def build(k, couplings):
    ns = f"urn:test:{uuid.uuid4().hex[:8]}"
    me = k.ensure_node("agent", f"{ns}/me")
    ctx = k.create_context("system", "thermostat loop", iri=f"{ns}/sys")
    f = lambda s, p, o=None, v=None, **args: k.assert_(s, p, o, v, context=ctx, method="inferred",  # noqa: E731
                                                       confidence=1, asserted_by=me, args=args)
    ports = {}
    for inst, typ in (("sensing", "cl:Sensor"), ("control", "cl:Controller"), ("actuation", "cl:Actuator")):
        r = k.ensure_node("role", f"{ns}/{inst}")
        f(r, "sys:instance_of", k.node_id(typ))
        for spec in k.schema.roles[k.iri(typ)]["ports"]:
            name, d = spec.split(":")
            ports[f"{inst}.{name}"] = k.ensure_node("port", f"{ns}/{inst}#{name}")
            f(r, "k:has_port", ports[f"{inst}.{name}"], dir=d)
        comp = k.ensure_node("thing", f"{ns}/impl/{inst}")
        f(comp, "k:is_a", k.node_id("sys:Component"))
        f(comp, "k:plays", r)
    for a, b in couplings:
        f(ports[a], "k:couples", ports[b], kind="stream")
    return ctx


def test_thermostat_system_validates(k):
    assert k.validate(build(k, [("sensing.reading", "control.input"), ("control.command", "actuation.power")])) == []


def test_backwards_coupling_is_caught(k):
    errors = k.validate(build(k, [("control.input", "sensing.reading")]))
    assert any("'from' end" in e for e in errors) and any("'to' end" in e for e in errors)


def test_role_without_type_is_caught(k):
    ctx = build(k, [])
    me = k.ensure_node("agent", "urn:test:me2")
    orphan = k.ensure_node("role", f"urn:test:{uuid.uuid4().hex[:8]}/orphan")
    k.assert_(orphan, "k:has_port", k.ensure_node("port", "urn:test:orphan#x"), context=ctx,
              method="inferred", confidence=1, asserted_by=me, args={"dir": "in"})
    assert any("has no urn:pc:systems:instance_of" in e for e in k.validate(ctx))

"""Component graphs for compiler tests, built in the kernel the way real systems are:
Reflexion from its spec.yaml (through the agent-spec view), and a thermostat loop by hand.
Both are run with the stub components in tests/components."""
import copy
import uuid
from pathlib import Path

import yaml

from modules.paper_compiler.spec_view import import_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "examples/reflexion/spec.yaml"
STUBS = "tests/components"
REFLEXION_STUBS = {"policy/codegen": "codegen", "eval/gen_tests": "gen_tests", "eval/run_tests": "run_tests",
                   "reflect/verbal": "reflect_verbal", "memory/sliding_window": "sliding_window",
                   "context/prepend_memory": "prepend_memory", "control/retry": "retry",
                   "memory/forgetful": "forgetful"}


def register_stubs(k, by, stubs=REFLEXION_STUBS, folder="reflexion"):
    """Components implemented by stub scripts: k:is_a sys:Component, sys:entrypoint."""
    ctx = k.create_context("world", "stub components")
    for ref, script in stubs.items():
        comp = k.ensure_node("thing", f"urn:pc:component:{ref}")
        kw = dict(context=ctx, method="defaulted", confidence=1, asserted_by=by)
        k.assert_(comp, "k:is_a", k.node_id("sys:Component"), **kw)
        k.assert_(comp, "sys:entrypoint", value=f"{STUBS}/{folder}/{script}.py", **kw)


def reflexion(k, spec_edit=None):
    """Import the Reflexion spec under a fresh paper id; returns (system IRI, agent)."""
    tag = uuid.uuid4().hex[:6]
    spec = copy.deepcopy(yaml.safe_load(SPEC.read_text()))
    spec["paper"]["id"] = f"reflexion-{tag}"
    if spec_edit:
        spec_edit(spec)
    by = k.ensure_node("agent", f"urn:test:{tag}:compiler-tests")
    register_stubs(k, by)
    return import_spec(k, spec, by=by), by


def thermostat(k, couplings=None, target=21.0):
    """sensing -> control -> actuation -> sensing, entered from the boundary; control.done
    ends the run; actuation reads the current temperature through a shared_state port."""
    tag = uuid.uuid4().hex[:6]
    ns = f"urn:test:{tag}"
    me = k.ensure_node("agent", f"{ns}/me")
    ctx = k.create_context("system", "thermostat", iri=f"{ns}/system")
    f = lambda s, p, o=None, v=None, **args: k.assert_(s, p, o, v, context=ctx, method="inferred",  # noqa: E731
                                                       confidence=1, asserted_by=me, args=args)
    ports = {}
    roles = (("boundary", "sys:Boundary", None), ("sensing", "cl:Sensor", "sensor"),
             ("control", "cl:Controller", "controller"), ("actuation", "cl:Actuator", "actuator"))
    for inst, typ, script in roles:
        r = k.ensure_node("role", f"{ns}/{inst}")
        f(r, "sys:instance_of", k.node_id(typ))
        for spec in k.schema.roles[k.iri(typ)]["ports"]:
            name, d = spec.split(":")[:2]
            ports[f"{inst}.{name}"] = k.ensure_node("port", f"{ns}/{inst}#{name}")
            f(r, "k:has_port", ports[f"{inst}.{name}"], dir=d)
        if script:
            comp = k.ensure_node("thing", f"{ns}/impl/{inst}")
            f(comp, "k:is_a", k.node_id("sys:Component"))
            f(comp, "sys:entrypoint", v=f"{STUBS}/thermostat/{script}.py")
            f(comp, "k:plays", r)
    ports["actuation.current"] = k.ensure_node("port", f"{ns}/actuation#current")
    f(k.node_id(f"{ns}/actuation"), "k:has_port", ports["actuation.current"], dir="in", extra=True, type="celsius")
    f(k.node_id(f"{ns}/control"), "sys:param", v=target, name="target")
    default = [("boundary.start", "sensing.sample", "event"), ("sensing.reading", "control.input", "event"),
               ("control.command", "actuation.power", "event"), ("actuation.applied", "sensing.sample", "event"),
               ("sensing.reading", "actuation.current", "shared_state"), ("control.done", "boundary.end", "event")]
    for a, b, kind in (couplings or default):
        f(ports[a], "k:couples", ports[b], kind=kind)
    return ctx, me

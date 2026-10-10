"""M3 kernel machinery on the toy thermostat domain: vocabularies and the schema checks they
drive, validate, promote, views and modules. No real module is loaded here."""
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from kernel import Kernel, KernelError, SchemaError, ValidationFailed

HERE = Path(__file__).parent


@pytest.fixture
def k(db_url, tmp_path):
    kern = Kernel.connect(db_url, data_root=tmp_path / "data")
    kern.load_vocabulary(HERE / "thermostat.yaml")
    yield kern
    kern.conn.close()


@pytest.fixture
def w(k):
    ns = f"urn:test:{uuid.uuid4().hex[:8]}:"

    class W:
        n = staticmethod(lambda name: ns + name)
        me = k.ensure_node("agent", ns + "me")
        sys_ctx = k.create_context("system", "loop", iri=ns + "sys")
        dev = k.ensure_node("thing", ns + "pid")
        heater = k.ensure_node("thing", ns + "heater")
    return W


def a(k, w, s, p, o=None, v=None, **kw):
    kw = {"context": w.sys_ctx, "method": "inferred", "confidence": 1, "asserted_by": w.me, **kw}
    return k.assert_(s, p, o, v, **kw)


def test_undeclared_predicate_is_refused(k, w):
    k.ensure_node("predicate", w.n("adhoc"))
    with pytest.raises(SchemaError, match="not declared"):
        a(k, w, w.dev, w.n("adhoc"), v=1)


def test_domain_range_enum_and_args(k, w):
    with pytest.raises(SchemaError, match="is not a urn:test:thermostat:Device"):
        a(k, w, w.dev, "th:mode", v="heat")                      # not yet typed
    a(k, w, w.heater, "k:is_a", k.node_id("th:Heater"))          # Heater is_a Device
    a(k, w, w.heater, "th:mode", v="heat")                       # subtype satisfies domain
    with pytest.raises(SchemaError, match="not one of"):
        a(k, w, w.heater, "th:mode", v="cool")
    a(k, w, w.dev, "k:is_a", k.node_id("th:Device"))
    with pytest.raises(SchemaError, match="requires args"):
        a(k, w, w.dev, "th:controls", w.heater)
    a(k, w, w.dev, "th:controls", w.heater, args={"loop": "main"})
    with pytest.raises(SchemaError, match="is not a urn:test:thermostat:Heater"):
        a(k, w, w.heater, "th:controls", w.dev, args={"loop": "main"})
    with pytest.raises(SchemaError, match="takes a literal"):
        a(k, w, w.heater, "th:watts", w.dev)


def test_validate_required_max_and_python_constraints(k, w):
    a(k, w, w.dev, "k:is_a", k.node_id("th:Device"))
    a(k, w, w.heater, "k:is_a", k.node_id("th:Heater"))
    a(k, w, w.heater, "th:mode", v="heat")
    a(k, w, w.heater, "th:mode", v="off", status="staged")
    errors = k.validate(w.sys_ctx)
    assert any(e.endswith(f"has no {k.iri('th:mode')}") and w.n("pid") in e for e in errors)
    assert any("(max 1)" in e for e in errors)
    assert any("heaters_have_watts is declared but not registered" in e for e in errors)

    def heaters_have_watts(kern, ctx, rows):
        has = {r.subject for r in rows if r.predicate == kern.iri("th:watts")}
        heaters = {r.subject for r in rows if r.predicate == kern.iri("k:is_a") and r.object == kern.iri("th:Heater")}
        return [f"{h} has no wattage" for h in sorted(heaters - has)]
    k.register_constraint("thermostat", "heaters_have_watts", heaters_have_watts)
    assert f"{w.n('heater')} has no wattage" in k.validate(w.sys_ctx)


def test_promote_only_when_valid(k, w):
    a(k, w, w.dev, "k:is_a", k.node_id("th:Device"))
    staged = a(k, w, w.dev, "th:status", v="ok", status="staged")
    k.register_constraint("thermostat", "heaters_have_watts", lambda *_: [])
    with pytest.raises(ValidationFailed, match="has no"):
        k.promote(w.sys_ctx, by=w.me)                            # pid has no mode yet
    a(k, w, w.dev, "th:mode", v="off", status="staged")
    assert k.promote(w.sys_ctx, by=w.me) == 2
    assert k.get(staged).status == "accepted"
    assert k.why(staged)["status_history"][-1]["reason"] == "promoted"


def test_views_round_trip(k, w):
    def export(kern, ctx):
        return {r.subject.rsplit(":", 1)[-1]: r.value for r in kern.query(context=ctx, predicate="th:status")}

    def import_(kern, doc, ns, by):
        ctx = kern.create_context("perspective", "doc", iri=ns + "doc")
        for name, status in doc.items():
            kern.assert_(kern.ensure_node("thing", ns + name), "th:status", value=status, context=ctx,
                         method="inferred", confidence=1, asserted_by=by)
        return ctx
    k.register_view("status-sheet", export, import_)
    doc = {"pid": "ok", "heater": "heater broken"}
    ctx = k.import_("status-sheet", doc, ns=w.n(""), by=w.me)
    assert k.export("status-sheet", ctx) == doc
    with pytest.raises(KernelError, match="no view"):
        k.export("nope", ctx)


def test_vocabulary_rules(k, tmp_path):
    changed = tmp_path / "thermostat.yaml"
    changed.write_text((HERE / "thermostat.yaml").read_text() + "\n# edited\n")
    with pytest.raises(SchemaError, match="bump its version"):
        k.load_vocabulary(changed)
    orphan = tmp_path / "orphan.yaml"
    orphan.write_text("vocabulary: orphan\nversion: '1'\nimports: [nothing]\nprefix: {o: 'urn:o:'}\n")
    with pytest.raises(SchemaError, match="not loaded"):
        k.load_vocabulary(orphan)


def test_schema_survives_reconnect(k, db_url, w):
    fresh = Kernel.connect(db_url, data_root=k.data_root)
    assert fresh.iri("th:mode") == "urn:test:thermostat:mode"
    a(fresh, w, w.dev, "k:is_a", fresh.node_id("th:Device"))
    with pytest.raises(SchemaError):
        a(fresh, w, w.dev, "th:mode", v="cool")
    fresh.conn.close()


def test_load_module_with_dependencies(k, tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    root = tmp_path / "mods"
    for name, deps, body in (("base", [], "calls.append('base')"), ("top", ["base"], "calls.append('top')")):
        d = root / name
        (d / f"pkg_{name}_{tag}").mkdir(parents=True)
        (d / "module.yaml").write_text(textwrap.dedent(f"""
            name: {name}
            version: "1"
            depends: {deps}
            vocab: [vocab.yaml]
            python: pkg_{name}_{tag}
        """))
        (d / "vocab.yaml").write_text(f"vocabulary: {name}{tag}\nversion: '1'\nimports: [kernel]\n"
                                      f"prefix: {{{name}{tag}: 'urn:test:{name}{tag}:'}}\n")
        (d / f"pkg_{name}_{tag}" / "__init__.py").write_text(
            f"calls = []\ndef register(kernel):\n    import builtins\n"
            f"    builtins.__dict__.setdefault('_mod_calls', []).append('{name}')\n")
        monkeypatch.syspath_prepend(str(d))
    k.load_module("top", search=[root])
    import builtins
    assert builtins.__dict__.pop("_mod_calls") == ["base", "top"]
    assert {"base", "top"} <= set(k.modules) and f"top{tag}" in k.schema.vocabularies
    with pytest.raises(KernelError, match="not found"):
        k.load_module("missing", search=[root])

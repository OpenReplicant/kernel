"""The `agent-spec` view: spec.yaml <-> kernel contexts.

import_ writes a spec into two contexts:
- a closed `system` context (`urn:pc:paper:<id>/system`): slot instances (role nodes,
  sys:instance_of an agent-design slot type), their ports and couplings, bindings (k:plays),
  params, prompts, assets, models, evaluation settings, behaviour tests, fidelity, compat;
- a `perspective` context (`urn:pc:paper:<id>/claims`) that the paper k:holds: bibliographic
  facts, the mechanism summary and claims, and the paper's reported results.

Provenance labels map to methods (stated and repo -> `stated`, staged until the paper and repo
are stored as sources; inferred; defaulted); a field without a label is `inferred` (the spec
author's reading). The label itself is kept as a `provenance` argument, list order as
`position`, so export rebuilds the document exactly.
"""
from modules.agent_design import slot_type_by_key, slot_types
from modules.systems import ports_of

IMPORTER = "urn:pc:agent:agent-spec-view"
SCHEMA_VERSION = "0.1"
TASK = "task"     # spec.yaml's name for the system boundary in wiring
METHOD = {"stated": "stated", "repo": "stated", "inferred": "inferred", "defaulted": "defaulted"}


def _base(paper_id: str) -> str:
    return f"urn:pc:paper:{paper_id}"


class _Writer:
    def __init__(self, k, by):
        self.k, self.by = k, by

    def node(self, kind, iri, type_=None):
        nid = self.k.ensure_node(kind, iri)
        if type_:
            self.fact(nid, "k:is_a", self.k.node_id(type_), ctx=self.ctx)
        return nid

    def fact(self, s, p, o=None, v=None, *, ctx=None, prov=None, **args):
        method = METHOD[prov] if prov else "inferred"
        args = {k_: a for k_, a in args.items() if a is not None}
        if prov:
            args["provenance"] = prov
        return self.k.assert_(s, p, o, v, context=ctx or self.ctx, method=method, confidence=0.9,
                              asserted_by=self.by, args=args,
                              status="staged" if method == "stated" else "accepted")


def import_spec(k, spec: dict, by=None) -> str:
    """Write a spec into the kernel; returns the system context's IRI."""
    by = by or k.ensure_node("agent", IMPORTER, "agent-spec view importer")
    paper = spec["paper"]
    base = _base(paper["id"])
    sysi = f"{base}/system"
    w = _Writer(k, by)
    system = k.create_context("system", f"{paper['title']} (system)", iri=sysi)
    claims_ctx = k.create_context("perspective", f"according to {paper['title']}", iri=f"{base}/claims")

    # The paper and what it claims, in its perspective.
    w.ctx = claims_ctx
    p = w.node("thing", base, "pc:Paper")
    w.fact(p, "k:holds", claims_ctx)
    w.fact(p, "pc:describes_system", system)
    for key in ("title", "year", "venue", "arxiv"):
        if key in paper:
            w.fact(p, f"pc:{key}", v=paper[key])
    for i, author in enumerate(paper["authors"]):
        w.fact(p, "pc:author", v=author, position=i)
    for i, src in enumerate(paper["sources"]):
        w.fact(p, "pc:source_ref", v=src, position=i)
    mech = spec["mechanism"]
    w.fact(p, "pc:summary", v=mech["summary"])
    claim_nodes = {}
    for i, text in enumerate(mech["claims"]):
        c = w.node("thing", f"{base}/claim/{i}", "pc:Claim")
        w.fact(c, "pc:text", v=text)
        w.fact(p, "pc:claims", c, position=i)
        claim_nodes.setdefault(text, c)
    for i, r in enumerate(spec["evaluation"].get("paper_results", [])):
        w.fact(p, "ev:reported_result", v=r["value"], metric=r["metric"], condition=r["condition"],
               source_ref=r.get("source_ref"), position=i)

    # The system.
    w.ctx = system
    role_iri = lambda sid: f"{sysi}/boundary" if sid == TASK else f"{sysi}/slot/{sid}"   # noqa: E731
    boundary = w.node("role", role_iri(TASK))
    w.fact(boundary, "sys:instance_of", k.node_id("sys:Boundary"))
    for name, d in ports_of(k, k.iri("sys:Boundary")).items():
        w.fact(boundary, "k:has_port", w.node("port", f"{role_iri(TASK)}#{name}"), dir=d)

    asset_iri = lambda aid: f"{sysi}/asset/{aid}"                                          # noqa: E731
    for i, a in enumerate(spec.get("assets", [])):
        node = w.node("thing", asset_iri(a["id"]), "sys:Asset")
        w.fact(node, "sys:asset_kind", v=a["kind"], prov=a.get("provenance"),
               source_ref=a.get("source_ref"), position=i)
        config = {key: a[key] for key in a if key not in ("id", "kind", "provenance", "source_ref")}
        if config:
            w.fact(node, "sys:asset_config", v=config)

    for i, s in enumerate(spec["slots"]):
        r = w.node("role", role_iri(s["id"]))
        stype = slot_type_by_key(k, s["slot"])
        w.fact(r, "sys:instance_of", k.node_id(stype), prov=s["provenance"],
               source_ref=s.get("source_ref"), position=i)
        for key, pred in (("scope", "ad:scope"), ("subtype", "ad:subtype"), ("mode", "ad:mode"),
                          ("order", "sys:order"), ("model_role", "ad:model_role")):
            if key in s:
                w.fact(r, pred, v=s[key])
        declared = ports_of(k, stype)
        extras = [x.split(":") for x in s.get("ports", [])]
        for j, (name, d) in enumerate(list(declared.items()) + extras):
            extra = j >= len(declared)
            w.fact(r, "k:has_port", w.node("port", f"{role_iri(s['id'])}#{name}"), dir=d,
                   extra=True if extra else None, position=j - len(declared) if extra else None)
        impl = s["implementation"]
        comp = w.node("thing", f"urn:pc:component:{impl['ref']}", "sys:Component")
        w.fact(comp, "k:plays", r, origin=impl["source"], kind=impl["kind"],
               entrypoint=impl.get("entrypoint"), wrapper=impl.get("wrapper"), asset=impl.get("asset"))
        for j, (name, param) in enumerate(s.get("params", {}).items()):
            w.fact(r, "sys:param", v=param["value"], prov=param["provenance"], name=name,
                   source_ref=param.get("source_ref"), sweep=param.get("sweep"), position=j)
        for j, pr in enumerate(s.get("prompts", [])):
            node = w.node("thing", f"{sysi}/prompt/{pr['id']}", "ad:Prompt")
            w.fact(node, "ad:prompt_file", v=pr["file"])
            w.fact(r, "ad:uses_prompt", node, prov=pr["provenance"], source_ref=pr.get("source_ref"),
                   position=j)
        for j, aid in enumerate(s.get("uses_assets", [])):
            w.fact(r, "sys:uses_asset", k.node_id(asset_iri(aid)), position=j)

    for i, edge in enumerate(spec["wiring"]):
        ends = []
        for end in (edge["from"], edge["to"]):
            sid, port = end.split(".")
            ends.append(w.node("port", f"{role_iri(sid)}#{port}"))   # an unknown slot's port has no owner
        w.fact(ends[0], "k:couples", ends[1], prov=edge.get("provenance"), kind="event",
               condition=edge.get("condition"), source_ref=edge.get("source_ref"), position=i)

    for i, m in enumerate(spec["models"]):
        model = w.node("thing", f"urn:pc:model:{m['use_model']}", "ev:Model")
        w.fact(system, "ev:uses_model", model, prov=m["provenance"], model_role=m["role"], position=i)
        w.fact(system, "pc:paper_model", v=m["paper_model"], model_role=m["role"])
        for j, (name, param) in enumerate(m.get("params", {}).items()):
            w.fact(system, "ev:model_param", v=param["value"], prov=param["provenance"],
                   model_role=m["role"], name=name, source_ref=param.get("source_ref"),
                   sweep=param.get("sweep"), position=j)

    ev = spec["evaluation"]
    bench = w.node("thing", f"urn:pc:benchmark:{ev['benchmark']}", "ev:Benchmark")
    w.fact(system, "ev:evaluated_on", bench)
    for key in ("harness_asset", "split", "subset", "metrics", "baselines"):
        if key in ev:
            w.fact(system, "ev:setting", v=ev[key], name=key)
    w.fact(system, "ev:acceptance", v=ev["acceptance"])

    for i, t in enumerate(spec["mechanism_tests"]):
        node = w.node("thing", f"{sysi}/test/{t['id']}", "sys:BehaviorTest")
        w.fact(node, "sys:test_body", v={key: t[key] for key in ("given", "within", "expect", "negate") if key in t},
               position=i)
        claim = claim_nodes.get(t["claim"])
        if claim is None:                       # a claim the mechanism section doesn't list
            w.ctx = claims_ctx
            claim = w.node("thing", f"{base}/claim/test-{t['id']}", "pc:Claim")
            w.fact(claim, "pc:text", v=t["claim"])
            w.ctx = system
        w.fact(node, "sys:tests_claim", claim)

    fid = spec["fidelity"]
    w.fact(system, "pc:fidelity", v=fid["overall"])
    for i, gap in enumerate(fid["gaps"]):
        w.fact(system, "pc:fidelity_gap", v=gap, position=i)
    if "compat" in spec:
        w.fact(system, "pc:compat", v=spec["compat"])
    return sysi


# -- export -------------------------------------------------------------------------------

def _by(rows, k, pred):
    p = k.iri(pred)
    return sorted((r for r in rows if r.predicate == p), key=lambda r: r.args.get("position", 0))


def _with_prov(d: dict, r) -> dict:
    if "provenance" in r.args:
        d["provenance"] = r.args["provenance"]
    return d


def _param(r) -> dict:
    out = _with_prov({"value": r.value}, r)
    for key in ("source_ref", "sweep"):
        if key in r.args:
            out[key] = r.args[key]
    return out


def _tail(iri: str, sep="/") -> str:
    return iri.rsplit(sep, 1)[-1]


def export_spec(k, system) -> dict:
    """Rebuild spec.yaml from a system context (and its paper's perspective context)."""
    states = ("accepted", "staged")
    srows = k.query(context=system, status=states)
    sysi = k.get(srows[0].id).context if srows else system
    base = sysi.rsplit("/system", 1)[0]
    prows = k.query(context=f"{base}/claims", status=states)
    one = lambda rows, subj, pred: next((r for r in rows if r.subject == subj and r.predicate == k.iri(pred)), None)  # noqa: E731
    of = lambda rows, subj: [r for r in rows if r.subject == subj]                                                     # noqa: E731

    # Paper and mechanism
    pr = of(prows, base)
    paper = {"id": _tail(base, ":")}
    for key in ("title",):
        paper[key] = one(prows, base, f"pc:{key}").value
    paper["authors"] = [r.value for r in _by(pr, k, "pc:author")]
    for key in ("year", "venue", "arxiv"):
        r = one(prows, base, f"pc:{key}")
        if r:
            paper[key] = r.value
    paper["sources"] = [r.value for r in _by(pr, k, "pc:source_ref")]
    text = {r.subject: r.value for r in prows if r.predicate == k.iri("pc:text")}
    mechanism = {"summary": one(prows, base, "pc:summary").value,
                 "claims": [text[r.object] for r in _by(pr, k, "pc:claims")]}

    # Slots
    types = slot_types(k)
    slots = []
    for inst in _by(srows, k, "sys:instance_of"):
        if inst.object not in types:
            continue                                             # the boundary
        r, rr = inst.subject, of(srows, inst.subject)
        s = {"id": _tail(r), "slot": types[inst.object]["key"]}
        for key, pred in (("subtype", "ad:subtype"), ("scope", "ad:scope"), ("mode", "ad:mode"),
                          ("order", "sys:order")):
            f = one(srows, r, pred)
            if f:
                s[key] = f.value
        plays = next(x for x in srows if x.predicate == k.iri("k:plays") and x.object == r)
        impl = {"source": plays.args["origin"], "kind": plays.args["kind"],
                "ref": plays.subject.removeprefix("urn:pc:component:")}
        for key in ("entrypoint", "wrapper", "asset"):
            if key in plays.args:
                impl[key] = plays.args[key]
        s["implementation"] = impl
        extras = [x for x in _by(rr, k, "k:has_port") if x.args.get("extra")]
        if extras:
            s["ports"] = [f"{_tail(x.object, '#')}:{x.args['dir']}" for x in extras]
        params = _by(rr, k, "sys:param")
        if params:
            s["params"] = {x.args["name"]: _param(x) for x in params}
        prompts = []
        for x in _by(rr, k, "ad:uses_prompt"):
            pr_ = {"id": _tail(x.object), "file": one(srows, x.object, "ad:prompt_file").value}
            if "source_ref" in x.args:
                pr_["source_ref"] = x.args["source_ref"]
            prompts.append(_with_prov(pr_, x))
        if prompts:
            s["prompts"] = prompts
        assets = [_tail(x.object) for x in _by(rr, k, "sys:uses_asset")]
        if assets:
            s["uses_assets"] = assets
        mr = one(srows, r, "ad:model_role")
        if mr:
            s["model_role"] = mr.value
        _with_prov(s, inst)
        if "source_ref" in inst.args:
            s["source_ref"] = inst.args["source_ref"]
        slots.append(s)

    # Wiring
    def end(port):
        role, name = port.rsplit("#", 1)
        return f"{TASK if role.endswith('/boundary') else _tail(role)}.{name}"
    wiring = []
    for x in _by(srows, k, "k:couples"):
        e = {"from": end(x.subject), "to": end(x.object)}
        for key in ("condition", "source_ref"):
            if key in x.args:
                e[key] = x.args[key]
        wiring.append(_with_prov(e, x))

    # Assets
    assets = []
    for x in _by(srows, k, "sys:asset_kind"):
        a = {"id": _tail(x.subject), "kind": x.value}
        cfg = one(srows, x.subject, "sys:asset_config")
        if cfg:
            a.update(cfg.value)
        if "source_ref" in x.args:
            a["source_ref"] = x.args["source_ref"]
        assets.append(_with_prov(a, x))

    # Models
    sysrows = of(srows, sysi)
    paper_model = {x.args["model_role"]: x.value for x in _by(sysrows, k, "pc:paper_model")}
    models = []
    for x in _by(sysrows, k, "ev:uses_model"):
        role = x.args["model_role"]
        m = {"role": role, "paper_model": paper_model[role], "use_model": x.object.removeprefix("urn:pc:model:")}
        params = [p_ for p_ in _by(sysrows, k, "ev:model_param") if p_.args["model_role"] == role]
        if params:
            m["params"] = {p_.args["name"]: _param(p_) for p_ in params}
        models.append(_with_prov(m, x))

    # Evaluation
    ev = {"benchmark": one(srows, sysi, "ev:evaluated_on").object.removeprefix("urn:pc:benchmark:")}
    for x in _by(sysrows, k, "ev:setting"):
        ev[x.args["name"]] = x.value
    results = []
    for x in _by(pr, k, "ev:reported_result"):
        res = {"metric": x.args["metric"], "value": x.value, "condition": x.args["condition"]}
        if "source_ref" in x.args:
            res["source_ref"] = x.args["source_ref"]
        results.append(res)
    ev["paper_results"] = results
    ev["acceptance"] = one(srows, sysi, "ev:acceptance").value

    # Mechanism tests
    tests = []
    for x in _by(srows, k, "sys:test_body"):
        claim = one(srows, x.subject, "sys:tests_claim").object
        tests.append({"id": _tail(x.subject), "claim": text[claim], **x.value})

    spec = {"schema_version": SCHEMA_VERSION, "paper": paper, "mechanism": mechanism, "slots": slots,
            "assets": assets, "wiring": wiring, "models": models, "evaluation": ev,
            "mechanism_tests": tests,
            "fidelity": {"overall": one(srows, sysi, "pc:fidelity").value,
                         "gaps": [x.value for x in _by(sysrows, k, "pc:fidelity_gap")]}}
    compat = one(srows, sysi, "pc:compat")
    if compat:
        spec["compat"] = compat.value
    return spec

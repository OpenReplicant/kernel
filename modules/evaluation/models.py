"""Models known to the kernel: the catalog, which model a system uses, and what a call costs.

- load_catalog(kernel, path, by): records models.yaml as `stated` facts with spans into it.
- use_model(kernel, system, model_role, model, by): names the model a system uses for a role.
- resolve(kernel, system, model_role, overrides): everything a call needs (model id, endpoint,
  key env var, parameters, prices), read from the kernel. A different model, endpoint or
  parameter is a different assertion, not different code.
- cost_usd(prices, usage) and record_spend(kernel, run_id, trace, by) for the run's account.
"""
from pathlib import Path

import yaml

CATALOG = Path(__file__).parent / "models.yaml"
CATALOG_CONTEXT = "urn:pc:catalog:models"


class BudgetExceeded(Exception):
    """The run has spent its budget; the model call is refused."""


def model_iri(model_id: str) -> str:
    return f"urn:pc:model:{model_id}"


def endpoint_iri(endpoint_id: str) -> str:
    return f"urn:pc:endpoint:{endpoint_id}"


# -- catalog ---------------------------------------------------------------------------

def _entries(text: str, key: str):
    """(entry, first line, last line) for each item of a top-level list, 1-based lines."""
    lines = text.splitlines()
    root = yaml.compose(text)
    for k_node, v_node in root.value:
        if k_node.value == key:
            for item in v_node.value:
                first = item.start_mark.line + 1
                last = item.end_mark.line if item.end_mark.column == 0 else item.end_mark.line + 1
                while last > first and (not lines[last - 1].strip() or lines[last - 1].lstrip().startswith("#")):
                    last -= 1
                yield yaml.safe_load(yaml.serialize(item)), first, last


def _put(kernel, ctx, subject, predicate, *, object=None, value=None, args=None, span, by) -> bool:
    """Assert a catalog fact unless the catalog already says it; supersede a changed one.
    Facts are matched by subject, predicate and args. Returns True if anything was written."""
    args = args or {}
    target = kernel.iri(object) if isinstance(object, str) else object
    for a in kernel.query(subject=subject, predicate=predicate, context=ctx, include_subcontexts=False):
        if {k: v for k, v in a.args.items()} != args:
            continue
        same = (a.object == target) if object is not None else (a.value == value)
        if same:
            return False
        new = {"object": kernel.node_id(object)} if object is not None else {"value": value}
        kernel.supersede(a.id, asserted_by=by, reason="catalog changed", evidence=[span], **new)
        return True
    kernel.assert_(subject, predicate, kernel.node_id(object) if object is not None else None, value,
                   context=ctx, method="stated", confidence=1, asserted_by=by, evidence=[span],
                   args=args)
    return True


def load_catalog(kernel, path=CATALOG, by=None) -> str:
    """Record a model catalog in the kernel; returns the catalog source's sha256. Idempotent."""
    path = Path(path)
    by = by or kernel.ensure_node("agent", "urn:pc:agent:catalog-loader", "model catalog loader")
    sha = kernel.put_source(path, "application/yaml")
    ctx = kernel.create_context("perspective", "model catalog", iri=CATALOG_CONTEXT)
    src = kernel.source_node(sha)
    if not kernel.query(subject=src, predicate="k:holds", object=ctx):
        kernel.assert_(src, "k:holds", ctx, context=ctx, method="defaulted", confidence=1, asserted_by=by)
    text = path.read_text()
    span = lambda a, b: kernel.add_span(sha, {"path": path.name, "line_start": a, "line_end": b})  # noqa: E731

    for e, a, b in _entries(text, "endpoints"):
        node = kernel.ensure_node("thing", endpoint_iri(e["id"]), e["id"])
        s = span(a, b)
        _put(kernel, ctx, node, "k:is_a", object="ev:Endpoint", span=s, by=by)
        for pred in ("api", "base_url", "key_env"):
            if pred in e:
                _put(kernel, ctx, node, f"ev:{pred}", value=e[pred], span=s, by=by)
    for e, a, b in _entries(text, "models"):
        node = kernel.ensure_node("thing", model_iri(e["id"]), e["id"])
        s = span(a, b)
        _put(kernel, ctx, node, "k:is_a", object="ev:Model", span=s, by=by)
        _put(kernel, ctx, node, "ev:model_id", value=e.get("model_id", e["id"]), span=s, by=by)
        _put(kernel, ctx, node, "ev:served_by", object=endpoint_iri(e["endpoint"]), span=s, by=by)
        for kind, usd in (e.get("prices") or {}).items():
            _put(kernel, ctx, node, "ev:price", value=usd, args={"per": kind}, span=s, by=by)
        for name, v in (e.get("params") or {}).items():
            _put(kernel, ctx, node, "ev:default_param", value=v, args={"name": name}, span=s, by=by)
    return sha


# -- which model a system uses -----------------------------------------------------------

def use_model(kernel, system, model_role: str, model, by, method: str = "defaulted",
              params: dict | None = None, evidence=()):
    """Name the model a system uses for a role (default policy: `defaulted`), with optional
    per-role parameters. A different choice later is a supersede, not an edit."""
    model_node = kernel.node_id(model_iri(model) if ":" not in str(model) else model)
    kw = dict(context=system, method=method, confidence=1, asserted_by=by, evidence=evidence)
    aid = kernel.assert_(system, "ev:uses_model", model_node, args={"model_role": model_role}, **kw)
    for name, v in (params or {}).items():
        kernel.assert_(system, "ev:model_param", value=v, args={"model_role": model_role, "name": name}, **kw)
    return aid


def _latest(rows):
    return rows[-1] if rows else None


def resolve(kernel, system, model_role: str, overrides: dict | None = None) -> dict:
    """What a call for `model_role` in this system uses: model, endpoint, parameters, prices.
    Parameters layer as model defaults < the system's ev:model_param < the call's overrides."""
    uses = [a for a in kernel.query(context=system, predicate="ev:uses_model")
            if a.args.get("model_role") == model_role]
    if not uses:
        raise LookupError(f"system {system} names no model for role {model_role!r} (ev:uses_model)")
    if len({a.object for a in uses}) > 1:
        raise LookupError(f"system {system} names {len(uses)} models for role {model_role!r}")
    model = uses[-1].object
    facts = lambda subj, pred: kernel.query(subject=subj, predicate=pred)  # noqa: E731
    model_id = _latest(facts(model, "ev:model_id"))
    endpoint = _latest(facts(model, "ev:served_by"))
    if not model_id or not endpoint:
        raise LookupError(f"model {model} has no ev:model_id or ev:served_by; load the catalog")
    ep = endpoint.object
    ep_fact = lambda pred: getattr(_latest(facts(ep, pred)), "value", None)  # noqa: E731
    params = {a.args["name"]: a.value for a in facts(model, "ev:default_param")}
    params.update({a.args["name"]: a.value for a in kernel.query(context=system, predicate="ev:model_param")
                   if a.args.get("model_role") == model_role})
    params.update(overrides or {})
    return {"model": model, "model_id": model_id.value, "endpoint": ep, "api": ep_fact("ev:api"),
            "base_url": ep_fact("ev:base_url"), "key_env": ep_fact("ev:key_env"), "params": params,
            "prices": {a.args["per"]: a.value for a in facts(model, "ev:price")}}


# -- cost and spend ----------------------------------------------------------------------

def cost_usd(prices: dict, usage: dict) -> float:
    """Cost of one call. Unknown cache prices are charged high (see models.yaml)."""
    if "input" not in prices or "output" not in prices:
        return 0.0                                      # an unpriced (e.g. local) model
    p_in = prices["input"]
    per_m = (usage.get("input_tokens", 0) * p_in
             + usage.get("output_tokens", 0) * prices["output"]
             + usage.get("cache_read_input_tokens", 0) * prices.get("cache_read", p_in)
             + usage.get("cache_creation_input_tokens", 0) * prices.get("cache_write", 1.25 * p_in))
    return round(per_m / 1_000_000, 8)


def record_spend(kernel, run_id: str, recorded: dict, by) -> list:
    """Observed facts about a run's model use (call count, spend) in its session context,
    each with spans on the llm.response lines of its recorded trace (systems.runs.record_run)."""
    lines = [e for e in recorded["events"] if e["event"] == "llm.response"]
    spans = [kernel.add_span(recorded["sha256"], {"path": "trace.jsonl", "line_start": e["line"],
                                                  "line_end": e["line"]}) for e in lines]
    if not spans:
        return []
    spent = round(sum(e["payload"].get("cost_usd", 0) for e in lines), 8)
    kw = dict(context=recorded["context"], method="observed", confidence=1, asserted_by=by, evidence=spans)
    return [kernel.assert_(recorded["context"], "ev:metric", value=len(lines), args={"name": "llm_calls"}, **kw),
            kernel.assert_(recorded["context"], "ev:metric", value=spent, args={"name": "llm_spend_usd"}, **kw)]

"""Agent-design module (L2): slot types are role types; these are their rules."""
VOCAB = "agent-design"


def slot_types(kernel) -> dict[str, dict]:
    """Slot type IRI -> its declaration (key, cardinality, internal, ports, ...)."""
    return {iri: r for iri, r in kernel.schema.roles.items() if r["vocab"] == VOCAB}


def slot_type_by_key(kernel, key: str) -> str | None:
    return next((iri for iri, r in slot_types(kernel).items() if r["key"] == key), None)


def _slots(k, rows):
    """Role instance -> slot type, for instances of agent-design slot types."""
    types = slot_types(k)
    return {r.subject: r.object for r in rows
            if r.predicate == k.iri("sys:instance_of") and r.object in types}


def slots_have_scope(k, ctx, rows):
    scoped = {r.subject for r in rows if r.predicate == k.iri("ad:scope")}
    return [f"slot {s} has no ad:scope" for s in sorted(set(_slots(k, rows)) - scoped)]


def exclusive_slots(k, ctx, rows):
    """An exclusive slot type has at most one instance per system."""
    types, count = slot_types(k), {}
    for inst, t in _slots(k, rows).items():
        count.setdefault(t, []).append(inst)
    return [f"{types[t]['key']} is exclusive but has {len(insts)} instances: {sorted(insts)}"
            for t, insts in sorted(count.items())
            if types[t].get("cardinality") == "exclusive" and len(insts) > 1]


def mcp_only_for_model_invoked(k, ctx, rows):
    """An internal slot (evaluator, reflector, controller, ...) must never be a tool the model
    under test can choose to call; a chain calling an MCP tool to implement it is fine."""
    types, slots = slot_types(k), _slots(k, rows)
    return [f"internal slot {r.object} ({types[slots[r.object]]['key']}) is bound as a model-invoked "
            f"mcp_tool: the model could skip or trigger it at will"
            for r in rows
            if r.predicate == k.iri("k:plays") and r.args.get("kind") == "mcp_tool"
            and r.object in slots and types[slots[r.object]].get("internal")]


def register(kernel):
    for fn in (slots_have_scope, exclusive_slots, mcp_only_for_model_invoked):
        kernel.register_constraint(VOCAB, fn.__name__, fn)

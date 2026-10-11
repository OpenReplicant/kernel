"""Systems module (L1): constraints every runnable system must meet, and helpers to read
role types' ports. graph.py reads a system context as a component graph for compilation."""

ANY = "any"


def parse_port(spec: str) -> tuple[str, str, str]:
    """'name:dir[:type]' -> (name, dir, type); an untyped port is `any`."""
    parts = spec.split(":")
    if len(parts) not in (2, 3) or parts[1] not in ("in", "out"):
        raise ValueError(f"port {spec!r} is not name:in|out[:type]")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ANY


def port_specs(kernel, role_type: str) -> dict[str, tuple[str, str]]:
    """A role type's minimum ports, name -> (direction, type), from its vocabulary."""
    return {n: (d, t) for n, d, t in map(parse_port, kernel.schema.roles.get(role_type, {}).get("ports", []))}


def ports_of(kernel, role_type: str) -> dict[str, str]:
    """A role type's minimum ports, name -> direction (in|out)."""
    return {n: d for n, (d, _) in port_specs(kernel, role_type).items()}


def port_types(kernel) -> dict[str, dict]:
    """Every port type the active vocabularies declare: name -> JSON Schema."""
    out = {}
    for vocab in sorted(kernel.schema.active):
        out.update(kernel.schema.vocabularies.get(vocab, {}).get("port_types") or {})
    return out


def compatible(out_type: str, in_type: str) -> bool:
    return in_type == ANY or out_type == in_type


def _ports(k, rows):
    """port IRI -> (role instance, dir, type, extra) for the ports declared in these rows."""
    instance_of = {r.subject: r.object for r in rows if r.predicate == k.iri("sys:instance_of")}
    out = {}
    for r in rows:
        if r.predicate != k.iri("k:has_port"):
            continue
        name = r.object.rsplit("#", 1)[-1]
        declared = port_specs(k, instance_of.get(r.subject, ""))
        typ = r.args.get("type") or declared.get(name, (None, ANY))[1]
        out[r.object] = (r.subject, r.args.get("dir"), typ, bool(r.args.get("extra")))
    return instance_of, out


def couplings_reference_declared_ports(k, ctx, rows):
    """Each port belongs to a role instance and is either one of its type's ports (same
    direction) or declared extra; each coupling runs from an out port to an in port."""
    instance_of, owner = _ports(k, rows)
    errors = []
    for port, (role, direction, _, extra) in sorted(owner.items()):
        if role not in instance_of or extra:
            continue
        declared = ports_of(k, instance_of[role])
        name = port.rsplit("#", 1)[-1]
        if name not in declared:
            errors.append(f"{role}: port '{name}' is not a port of {instance_of[role]}; declare it as extra")
        elif declared[name] != direction:
            errors.append(f"{role}: port '{name}' is {declared[name]} for {instance_of[role]}, not {direction}")
    for r in rows:
        if r.predicate != k.iri("k:couples"):
            continue
        for end, want, side in ((r.subject, "out", "from"), (r.object, "in", "to")):
            if end not in owner:
                errors.append(f"coupling {r.subject} -> {r.object}: port {end} belongs to no role instance in this system")
            elif owner[end][1] != want:
                errors.append(f"coupling {r.subject} -> {r.object}: '{side}' end {end} is an {owner[end][1]} port")
    return errors


def couplings_have_compatible_types(k, ctx, rows):
    """A coupling carries what its out port produces into an in port that accepts it: the
    same type, or `any`. Every port type must be declared by a loaded vocabulary."""
    _, owner = _ports(k, rows)
    known = port_types(k)
    errors = [f"port {p}: type '{t}' is not declared in any loaded vocabulary's port_types"
              for p, (_, _, t, _) in sorted(owner.items()) if t not in known]
    for r in rows:
        if r.predicate != k.iri("k:couples") or r.subject not in owner or r.object not in owner:
            continue
        out_t, in_t = owner[r.subject][2], owner[r.object][2]
        if not compatible(out_t, in_t):
            errors.append(f"coupling {r.subject} -> {r.object}: carries '{out_t}' into a port that takes '{in_t}'")
    return errors


def register(kernel):
    kernel.register_constraint("systems", "couplings_reference_declared_ports", couplings_reference_declared_ports)
    kernel.register_constraint("systems", "couplings_have_compatible_types", couplings_have_compatible_types)

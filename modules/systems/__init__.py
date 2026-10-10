"""Systems module (L1): constraints every runnable system must meet."""


def ports_of(kernel, role_type: str) -> dict[str, str]:
    """A role type's minimum ports, name -> direction (in|out), from its vocabulary."""
    return dict(p.split(":") for p in kernel.schema.roles.get(role_type, {}).get("ports", []))


def couplings_reference_declared_ports(k, ctx, rows):
    """Each port belongs to a role instance and is either one of its type's ports (same
    direction) or declared extra; each coupling runs from an out port to an in port."""
    instance_of = {r.subject: r.object for r in rows if r.predicate == k.iri("sys:instance_of")}
    owner = {r.object: (r.subject, r.args.get("dir"), bool(r.args.get("extra")))
             for r in rows if r.predicate == k.iri("k:has_port")}
    errors = []
    for port, (role, direction, extra) in sorted(owner.items()):
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


def register(kernel):
    kernel.register_constraint("systems", "couplings_reference_declared_ports", couplings_reference_declared_ports)

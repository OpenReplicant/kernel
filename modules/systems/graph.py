"""Read a system context as a component graph: plain data a compiler can turn into chains
(pclib.compile), with no kernel or runtime in it.

    {"system": iri,
     "instances": {id: {"iri", "role_type", "boundary", "ports": {name: {"dir", "type"}},
                        "component", "entrypoint", "params"}},
     "couplings": [{"from": [id, port], "to": [id, port], "kind"}],
     "port_types": {name: JSON Schema}}

An instance's id is the last segment of its role IRI. Its component is whatever `k:plays` it;
the entrypoint (a step script, relative to the repo) comes from the binding's `entrypoint`
argument, else from the component's `sys:entrypoint`.
"""
from . import ANY, port_specs, port_types


class GraphError(Exception):
    pass


def _id(iri: str) -> str:
    return iri.rstrip("/").rsplit("/", 1)[-1]


def read_graph(kernel, system, status=("accepted", "staged")) -> dict:
    """The component graph of a system context. Staged facts count by default: a design
    under review can be compiled and run before it is promoted."""
    rows = kernel.query(context=system, status=status)
    p = kernel.iri
    instance_of = {r.subject: r.object for r in rows if r.predicate == p("sys:instance_of")}
    ids = {}
    for iri in instance_of:
        iid = _id(iri)
        if iid in ids.values():
            raise GraphError(f"two role instances share the id {iid!r}")
        ids[iri] = iid
    boundary = p("sys:Boundary")
    instances = {}
    for iri, rtype in sorted(instance_of.items(), key=lambda kv: ids[kv[0]]):
        declared = port_specs(kernel, rtype)
        ports = {}
        for r in rows:
            if r.predicate == p("k:has_port") and r.subject == iri:
                name = r.object.rsplit("#", 1)[-1]
                ports[name] = {"dir": r.args.get("dir"),
                               "type": r.args.get("type") or declared.get(name, (None, ANY))[1]}
        plays = [r for r in rows if r.predicate == p("k:plays") and r.object == iri]
        component = plays[-1].subject if plays else None
        entrypoint = plays[-1].args.get("entrypoint") if plays else None
        if component and not entrypoint:
            ep = kernel.query(subject=component, predicate="sys:entrypoint")
            entrypoint = ep[-1].value if ep else None
        params = {r.args["name"]: r.value for r in rows
                  if r.predicate == p("sys:param") and r.subject == iri}
        instances[ids[iri]] = {"iri": iri, "role_type": rtype, "boundary": rtype == boundary,
                               "ports": ports, "component": component, "entrypoint": entrypoint,
                               "params": params}
    owner = {f"{iri}#{name}": (ids[iri], name)
             for iri in instance_of for name in instances[ids[iri]]["ports"]}
    couplings = []
    for r in rows:
        if r.predicate != p("k:couples"):
            continue
        if r.subject not in owner or r.object not in owner:
            raise GraphError(f"coupling {r.subject} -> {r.object} names a port no role instance has")
        couplings.append({"from": list(owner[r.subject]), "to": list(owner[r.object]),
                          "kind": r.args.get("kind", "event")})
    return {"system": kernel.iri(system) if isinstance(system, str) else str(system),
            "instances": instances, "couplings": couplings, "port_types": port_types(kernel)}

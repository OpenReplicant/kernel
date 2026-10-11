"""Compile a component graph (modules.systems.graph.read_graph) into RuleGo chains of
pclib components (pclib.component). Pure: graph data in, chain JSON out.

Semantics (docs/RULEGO_NOTES.md §9):
- Flow couplings (every kind but `shared_state`) order and trigger: the coupled-to instance
  runs after the port fires. A `shared_state` coupling is a read: the target sees the
  latest value written, and is neither triggered nor ordered by it.
- One message travels at a time: an out port has at most one flow coupling (no fan-out) and
  a step fires at most one flow port, so several flow couplings into one instance are just
  alternative routes into it.
- A cycle of flow couplings becomes a loop: a `while` node over an iteration chain, entered
  at the one instance with a flow coupling from outside the cycle. A coupling back to the
  entry ends an iteration; one leaving the cycle ends the loop (the adapter sets the loop's
  stop flag and ctl.exit, and a switch after the loop routes on ctl.exit). Nested cycles
  are refused.
- The boundary's start port holds the task; a coupling into its end port ends the run.
"""
from collections import defaultdict

from . import chains as c

SHARED = "shared_state"


class CompileError(Exception):
    pass


def _sccs(nodes, succ):
    """Strongly connected components (Tarjan), each sorted, in a deterministic order."""
    index, low, stack, on, out, counter = {}, {}, [], set(), [], [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(succ[v]):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(sorted(comp))
    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return out


def _topo(nodes, succ, what):
    """Nodes in dependency order; CompileError if the edges among them form a cycle."""
    indeg = {n: 0 for n in nodes}
    for n in nodes:
        for m in succ[n]:
            if m in indeg:
                indeg[m] += 1
    ready, order = sorted(n for n, d in indeg.items() if d == 0), []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(succ[n]):
            if m in indeg:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
    if len(order) != len(nodes):
        raise CompileError(f"{what}: {sorted(set(nodes) - set(order))} form a cycle inside a loop (nested loops are not supported)")
    return order


def plan(graph: dict) -> dict:
    """Check the graph against the semantics above and work out its units: plain steps and
    loops, each loop with its entry and members."""
    inst = graph["instances"]
    boundaries = [i for i, v in inst.items() if v["boundary"]]
    if len(boundaries) != 1:
        raise CompileError(f"a system needs exactly one boundary, found {boundaries}")
    b = boundaries[0]
    steps = sorted(i for i, v in inst.items() if not v["boundary"])
    for i in steps:
        if not inst[i]["entrypoint"]:
            raise CompileError(f"{i}: no component with an entrypoint plays this role")
    flow = [cp for cp in graph["couplings"] if cp["kind"] != SHARED]
    by_port = defaultdict(list)
    for cp in flow:
        by_port[tuple(cp["from"])].append(cp)
    for (u, port), cps in sorted(by_port.items()):
        if len(cps) > 1:
            raise CompileError(f"{u}.{port} has {len(cps)} flow couplings (fan-out is not supported; "
                               "make the extra ones shared_state reads, or add a port)")
    succ = {i: set() for i in steps}
    for cp in flow:
        u, v = cp["from"][0], cp["to"][0]
        if u in succ and v in succ:
            succ[u].add(v)
    unit_of, loops = {}, {}
    for comp in _sccs(steps, succ):
        if len(comp) == 1 and comp[0] not in succ[comp[0]]:
            unit_of[comp[0]] = comp[0]
            continue
        members = set(comp)
        entries = sorted({cp["to"][0] for cp in flow if cp["to"][0] in members and cp["from"][0] not in members})
        if len(entries) != 1:
            raise CompileError(f"loop over {comp} needs exactly one instance entered from outside it, found {entries}")
        entry = entries[0]
        inner = {m: {n for n in succ[m] if n in members and n != entry} for m in comp}
        order = _topo(comp, inner, f"loop entered at {entry}")
        lid = f"loop_{entry}"
        loops[lid] = {"entry": entry, "members": order}
        for m in comp:
            unit_of[m] = lid
    start = by_port.get((b, "start"), [])
    if not start:
        raise CompileError(f"nothing is coupled to {b}.start")
    first = unit_of[start[0]["to"][0]]
    usucc = defaultdict(set)
    for cp in flow:
        u, v = cp["from"][0], cp["to"][0]
        if u in unit_of and v in unit_of and unit_of[u] != unit_of[v]:
            usucc[unit_of[u]].add(unit_of[v])
    units = sorted(set(unit_of.values()))
    reach, todo = set(), [first]
    while todo:
        x = todo.pop()
        if x not in reach:
            reach.add(x)
            todo.extend(usucc[x])
    if set(units) - reach:
        raise CompileError(f"{sorted(set(units) - reach)} can't be reached from {b}.start")
    order = _topo(units, usucc, "units")
    order.remove(first)
    return {"boundary": b, "flow": flow, "unit_of": unit_of, "loops": loops, "units": [first] + order}


def _config(graph, p, i, max_iterations):
    inst, b = graph["instances"], p["boundary"]
    ports = inst[i]["ports"]
    inputs = defaultdict(list)
    for cp in graph["couplings"]:
        if cp["to"][0] == i:
            inputs[cp["to"][1]].append(f"{cp['from'][0]}.{cp['from'][1]}")
    routes, exits = [], []
    loop = p["unit_of"][i] if p["unit_of"][i] in p["loops"] else None
    for cp in p["flow"]:
        if cp["from"][0] != i:
            continue
        routes.append(cp["from"][1])
        v = cp["to"][0]
        leaves = v == b or (loop and p["unit_of"].get(v) != loop)
        if leaves:
            exits.append(cp["from"][1])
    out = {n: d["type"] for n, d in ports.items() if d["dir"] == "out"}
    config = {"inputs": {port: sorted(src) for port, src in sorted(inputs.items())}, "outputs": out, "routes": sorted(routes),
              "exits": sorted(exits), "params": inst[i]["params"],
              "types": {t: graph["port_types"].get(t, {}) for t in sorted(set(out.values()))}}
    if loop:
        config["loop"] = loop
        if p["loops"][loop]["entry"] == i:
            config.update(entry=True, max_iterations=max_iterations)
    return config


def compile_graph(graph: dict, name: str, max_iterations: int = 100, timeout: float = 300) -> list[dict]:
    """The chains for a graph: `name` (root) plus `name__<loop>` per loop. Write them with
    pclib.chains.write; start a run with initial_message()."""
    p = plan(graph)
    inst, b, unit_of = graph["instances"], p["boundary"], p["unit_of"]
    node = lambda i: c.component(i, inst[i]["entrypoint"], _config(graph, p, i, max_iterations), timeout)  # noqa: E731

    def routing(i, target_of):
        """A switch on ctl.port after instance i, to the targets in this chain."""
        cases = [(cp["from"][1], target_of(cp["to"][0])) for cp in p["flow"] if cp["from"][0] == i]
        cases = [(port, t) for port, t in sorted(cases) if t]
        if not cases:
            return [], []
        sw = f"{i}__route"
        return ([c.switch(sw, [(f'msg.ctl.port == "{port}"', port) for port, _ in cases])],
                [(i, sw)] + [(sw, t, port) for port, t in cases])

    chains, nodes, edges = [], [], []
    for u in p["units"]:
        if u in p["loops"]:
            lp = p["loops"][u]
            members = set(lp["members"])
            inodes, iedges = [], []
            for m in lp["members"]:
                inodes.append(node(m))
                sn, se = routing(m, lambda v: v if v in members and v != lp["entry"] else None)
                inodes += sn
                iedges += se
            chains.append(c.chain(f"{name}__{u}", inodes, iedges))
            nodes.append(c.loop(u, f"chain:{name}__{u}", f"msg.ctl.stop_{u} != true"))
            exits = sorted({(cp["from"][0], cp["from"][1], unit_of[cp["to"][0]]) for cp in p["flow"]
                            if cp["from"][0] in members and cp["to"][0] in unit_of and unit_of[cp["to"][0]] != u})
            if exits:
                sw = f"{u}__exit"
                nodes.append(c.switch(sw, [(f'msg.ctl.exit == "{m}.{port}"', f"{m}__{port}") for m, port, _ in exits]))
                edges += [(u, sw)] + [(sw, t, f"{m}__{port}") for m, port, t in exits]
        else:
            nodes.append(node(u))
            sn, se = routing(u, lambda v: unit_of.get(v) if unit_of.get(v) != u else None)
            nodes += sn
            edges += se
    chains.insert(0, c.chain(name, nodes, edges, root=True))
    return chains


def initial_message(graph: dict, task) -> dict:
    """The message a compiled root chain starts from: the task on the boundary's start port."""
    b = next(i for i, v in graph["instances"].items() if v["boundary"])
    return {"ports": {f"{b}.start": task}, "seq": {f"{b}.start": 0}, "state": {}, "ctl": {}}

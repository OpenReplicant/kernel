"""Components: steps that fill a role in a component graph, seen only through their ports.

A component is a script defining `main(inp)` like any step, but its input data is

    {"inputs": {in_port: value}, "params": {...}, "state": <its own state, or None>}

and it returns {"data": {"outputs": {out_port: value}, "state"?: ...}}, firing only the
ports it emits on. It never sees the rest of the system.

The adapter here (bound by stubs that pclib.compile generates) does the rest on the shared
message, the blackboard a compiled chain passes from node to node:

    {"ports": {"<instance>.<port>": value}, "seq": {key: n}, "state": {instance: ...},
     "ctl": {"port": fired routing port, "exit": "<instance>.<port>",
             "stop_<loop>": bool, "iter_<loop>": n}}

- inputs: each in port reads the latest value written by the ports coupled into it;
- outputs: checked against their port types (JSON Schema), written to the blackboard, and
  traced as `slot.emit` (slot = instance, port);
- routing: the one fired port with a flow coupling becomes ctl.port, which the compiled
  switch after the node routes on; a port leaving a loop also sets the loop's stop flag
  and ctl.exit;
- a loop's entry counts iterations (ctl.iter_<loop>, capped by max_iterations) and sets the
  episode to the iteration, so each pass round the loop is an episode in traces.
"""
import jsonschema

from .contract import StepInput, trace
from .node import _load, call_fn


class ComponentError(Exception):
    pass


def bind(script: str, instance: str, timeout: float | None = None, config: dict | None = None):
    """The Process function for a component node (warm-loads the script now)."""
    main = _load(script).main
    config = config or {}

    def Process(msg, metadata, msgType, dataType):
        return call_fn(lambda inp: run(main, instance, config, inp), instance, msg, metadata, timeout)
    return Process


def run(main, instance: str, config: dict, inp: StepInput) -> dict:
    bb = inp.data
    ports, seq = dict(bb.get("ports", {})), dict(bb.get("seq", {}))
    state, ctl = dict(bb.get("state", {})), dict(bb.get("ctl", {}))
    loop = config.get("loop")
    if config.get("entry"):
        n = ctl.get(f"iter_{loop}", 0) + 1
        if n > config.get("max_iterations", 100):
            raise ComponentError(f"loop {loop} passed {config.get('max_iterations', 100)} iterations")
        ctl[f"iter_{loop}"] = n

    def latest(sources):
        written = [s for s in sources if s in ports]
        return ports[max(written, key=lambda s: seq.get(s, 0))] if written else None
    inputs = {port: latest(sources) for port, sources in config.get("inputs", {}).items()}
    result = main(StepInput(run_id=inp.run_id, step_key=inp.step_key, task_id=inp.task_id,
                            episode=inp.episode, meta=inp.meta,
                            data={"inputs": inputs, "params": config.get("params", {}),
                                  "state": state.get(instance)}))
    data = (result or {}).get("data", {})
    outputs = data.get("outputs", {})
    declared, types = config.get("outputs", {}), config.get("types", {})
    counter = max(seq.values(), default=0)
    for port, value in outputs.items():
        if port not in declared:
            raise ComponentError(f"{instance}: fired '{port}', which is not one of its out ports {sorted(declared)}")
        try:
            jsonschema.validate(value, types.get(declared[port], {}))
        except jsonschema.ValidationError as e:
            raise ComponentError(f"{instance}.{port} ({declared[port]}): {e.message}") from None
        counter += 1
        ports[f"{instance}.{port}"], seq[f"{instance}.{port}"] = value, counter
        trace("slot.emit", slot=instance, port=port, payload=value)
    if "state" in data:
        state[instance] = data["state"]
    routed = [p for p in outputs if p in config.get("routes", ())]
    if len(routed) > 1:
        raise ComponentError(f"{instance}: fired {routed} together; only one flow port may fire per step")
    ctl["port"] = routed[0] if routed else None
    if routed and routed[0] in config.get("exits", ()):        # leaves its loop, or ends the task
        if loop:
            ctl[f"stop_{loop}"] = True
        ctl["exit"] = f"{instance}.{routed[0]}"
    out = {"data": {**bb, "ports": ports, "seq": seq, "state": state, "ctl": ctl}}
    if config.get("entry"):
        out["episode"] = ctl[f"iter_{loop}"]      # each pass round a loop is an episode (a trial)
    return out

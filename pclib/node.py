"""pclib as a RuleGo node: the entry point for `x/python` nodes.

Each step node runs a small generated stub file (pclib.chains.write() emits them next to the
chain JSON):

    from pclib.node import bind
    Process = bind("/repo/modules/agent_design/steps/reflect_verbal.py", "reflector", timeout=300)

x/python imports the stub when it pre-starts a worker, so pclib, psycopg and the step module
are loaded before a message arrives; Process then runs one step and the worker exits.

The message data is the step's `data`. RuleGo metadata (a flat string map) carries run_id,
task_id and episode, plus the step's `meta`. The step script is any file that defines
`main(inp)` (the same function a command-line step passes to run_step).
"""
import importlib.util
import json
import sys
from pathlib import Path

from .contract import StepInput, execute

# Metadata keys owned by the contract or by RuleGo itself; everything else is the step's meta.
RESERVED = {"run_id", "task_id", "episode", "step_key", "id", "msgType", "username"}

_modules: dict[str, object] = {}


def _load(script: str):
    if script not in _modules:
        spec = importlib.util.spec_from_file_location(f"_pc_step_{Path(script).stem}", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _modules[script] = mod
    return _modules[script]


def _meta_value(v):
    return v if isinstance(v, str) else json.dumps(v)


def bind(script: str, step: str, timeout: float | None = None):
    """The Process function for one step node, with the step module loaded now (warm)."""
    _load(script)

    def Process(msg, metadata, msgType, dataType):
        return call(script, step, msg, metadata, timeout)
    return Process


def call(script: str, step: str, msg, metadata: dict, timeout: float | None = None) -> dict:
    """Run one step for an x/python node and return {"msg": ..., "metadata": ...}."""
    if not metadata.get("run_id"):
        raise ValueError("metadata is missing run_id (pass it as a query parameter or set it upstream)")
    episode = int(metadata["episode"]) if metadata.get("episode") not in (None, "") else None
    loop = metadata.get("_loopIndex")
    key = f"task={metadata.get('task_id')}/ep={episode}/" + (f"i={loop}/" if loop not in (None, "") else "") + step
    inp = StepInput(run_id=metadata["run_id"], step_key=key, task_id=metadata.get("task_id"),
                    episode=episode, data=msg if isinstance(msg, dict) else {"value": msg},
                    meta={k: v for k, v in metadata.items() if k not in RESERVED and not k.startswith("_")})
    output = execute(_load(script).main, inp, timeout)
    out_meta = dict(metadata)
    out_meta.update({k: _meta_value(v) for k, v in output.get("meta", {}).items()})
    if output.get("episode") is not None:
        out_meta["episode"] = str(output["episode"])
    print(f"pclib.node {key}: ok", file=sys.stderr)
    return {"msg": output["data"], "metadata": out_meta}

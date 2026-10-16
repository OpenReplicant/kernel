"""Chain interpreter. No scheduling and no I/O of its own: HTTP goes through an injected function,
so the routing and templating can be tested without a network.

Identity: every call to the kernel API carries the token of the chain's `runs_as` agent.
Runs: each step that writes to the kernel (a non-GET call to KK_API) gets its own kernel run with
plan_step = the step IRI (<chain IRI>/<step id>), started before the step and finished after it.
`${run.id}` in that step's config is the run id (e.g. generated_by of a change set). Steps that
only read, and executions that do nothing, create no runs.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("chain_runner")

# http(method, url, token or None, body or None) -> (status code, parsed JSON or None)
Http = Callable[[str, str, Any, Any], tuple[int, Any]]

PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
SUPPORTED = {"http", "subchain", "log", "script", "filter", "switch", "transform"}


class StepError(Exception):
    pass


@dataclass
class Chain:
    iri: str
    runs_as: str
    trigger: dict
    steps: dict
    first: str
    module_dir: Path
    module: str

    @classmethod
    def from_dict(cls, d: dict, module_dir: Path) -> "Chain":
        steps = {s["id"]: s for s in d["steps"]}
        return cls(d["chain"], d["runs_as"], d["trigger"], steps, d["steps"][0]["id"], module_dir,
                   f"urn:kk:module:{module_dir.name}")


@dataclass
class Execution:
    chain: str
    ok: bool = True
    msg: Any = None
    trace: list = field(default_factory=list)   # [{step, label, ms, run?, error?}]
    runs: list = field(default_factory=list)


def lookup(path: str, scope: dict) -> Any:
    head, *rest = path.split(".")
    value = scope[head]
    for part in rest:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def render(value: Any, scope: dict) -> Any:
    """${a.b} placeholders. A string that is exactly one placeholder keeps the value's type."""
    if isinstance(value, dict):
        return {k: render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, scope) for v in value]
    if not isinstance(value, str):
        return value
    whole = PLACEHOLDER.fullmatch(value)
    if whole:
        return lookup(whole.group(1), scope)
    return PLACEHOLDER.sub(lambda m: str(lookup(m.group(1), scope)), value)


class Runner:
    def __init__(self, chains: dict[str, Chain], tokens: dict[str, str], api: str, http: Http,
                 max_steps: int = 100):
        self.chains, self.tokens, self.api, self.http, self.max_steps = chains, tokens, api.rstrip("/"), http, max_steps

    # -- kernel runs ---------------------------------------------------------------------------
    def _start_run(self, chain: Chain, step_iri: str, info: dict) -> str:
        code, body = self.http("POST", f"{self.api}/rpc/start_run", self.tokens[chain.runs_as],
                               {"module": chain.module, "plan_step": step_iri, "info": info})
        if code >= 300:
            raise StepError(f"start_run failed: {code} {body}")
        return body

    def _finish_run(self, chain: Chain, run_id: str, ok: bool, ms: int) -> None:
        self.http("POST", f"{self.api}/rpc/finish_run", self.tokens[chain.runs_as],
                  {"run_id": run_id, "status": "succeeded" if ok else "failed", "cost": {"ms": ms}})

    # -- steps ---------------------------------------------------------------------------------
    def _http(self, chain: Chain, step: dict, scope: dict, ex: Execution, entry: dict) -> tuple[str, Any]:
        cfg = step.get("with", {})
        method = cfg.get("method", "GET").upper()
        url = render(cfg["url"], scope)
        kernel = url.startswith(self.api)
        writes = kernel and method != "GET"
        run_id = None
        if writes:
            run_id = self._start_run(chain, f"{chain.iri}/{step['id']}",
                                     {"chain": chain.iri, "after": [t["step"] for t in ex.trace]})
            scope = {**scope, "run": {"id": run_id}}
            entry["run"] = run_id
            ex.runs.append(run_id)
        body = render(cfg["body"], scope) if "body" in cfg else (scope["msg"] if method != "GET" else None)
        started = time.monotonic()
        code, out = self.http(method, url, self.tokens[chain.runs_as] if kernel else None, body)
        if run_id:
            self._finish_run(chain, run_id, code < 300, int((time.monotonic() - started) * 1000))
        if code >= 300:
            entry["error"] = f"HTTP {code}: {json.dumps(out)[:300]}"
            return "failure", scope["msg"]
        return "success", out

    def _python(self, chain: Chain, step: dict, msg: Any) -> Any:
        cfg = step.get("with", {})
        lang = cfg.get("lang", "python")
        if lang != "python":
            raise StepError(f"{lang} steps are not supported by the Python runner")
        if "source" in cfg:
            env: dict = {}
            exec(f"def _f(msg):\n" + "\n".join("    " + l for l in cfg["source"].splitlines()), env)
            return env["_f"](msg)
        proc = subprocess.run(["python", str(chain.module_dir / cfg["path"])], input=json.dumps(msg),
                              capture_output=True, text=True, timeout=300, cwd=chain.module_dir)
        if proc.returncode != 0:
            raise StepError(f"script exited {proc.returncode}: {proc.stderr[-300:]}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def _step(self, chain: Chain, step: dict, msg: Any, ex: Execution, entry: dict) -> tuple[str, Any]:
        kind = step["do"]
        scope = {"msg": msg, "KK_API": self.api}
        if kind not in SUPPORTED:
            raise StepError(f"step type {kind} is not supported by the Python runner")
        if kind == "http":
            return self._http(chain, step, scope, ex, entry)
        if kind == "log":
            log.info("%s/%s: %s", chain.iri, step["id"], json.dumps(msg)[:500])
            return "success", msg
        if kind == "subchain":
            cfg = step.get("with", {})
            items = render(cfg["for_each"], scope) if "for_each" in cfg else [msg]
            results = [self.execute(cfg["chain"], item) for item in items]
            for r in results:
                ex.runs += r.runs
            entry["executions"] = len(results)
            errors = [t for r in results if not r.ok for t in r.trace if "error" in t]
            if errors:
                entry["sub_errors"] = errors[:3]
            return ("success" if all(r.ok for r in results) else "failure"), msg
        out = self._python(chain, step, msg)
        if kind == "filter":
            return ("true" if out else "false"), msg
        if kind == "switch":
            return str(out), msg
        return "success", out                              # script, transform

    # -- executions ----------------------------------------------------------------------------
    def execute(self, chain_iri: str, msg: Any = None) -> Execution:
        chain = self.chains[chain_iri]
        ex = Execution(chain_iri, msg=msg)
        if chain.runs_as not in self.tokens:
            ex.ok = False
            ex.trace.append({"step": None, "label": "failure", "error": f"no token for {chain.runs_as}"})
            return ex
        current = [chain.first]
        while current:
            if len(ex.trace) >= self.max_steps:
                ex.ok = False
                ex.trace.append({"step": None, "label": "failure", "error": "step limit reached"})
                break
            step_id = current.pop(0)
            step = chain.steps[step_id]
            entry = {"step": f"{chain.iri}/{step_id}"}
            started = time.monotonic()
            try:
                label, ex.msg = self._step(chain, step, ex.msg, ex, entry)
            except (StepError, KeyError, IndexError, ValueError, TypeError) as e:
                label = "failure"
                entry["error"] = f"{type(e).__name__}: {e}"
            entry["label"] = label
            entry["ms"] = int((time.monotonic() - started) * 1000)
            ex.trace.append(entry)
            nxt = step.get("next", {}).get(label)
            if label == "failure" and nxt is None:
                ex.ok = False                              # unhandled failure ends the execution
                break
            if nxt:
                current += [nxt] if isinstance(nxt, str) else list(nxt)
        if any("error" in t for t in ex.trace):
            ex.ok = False
        return ex

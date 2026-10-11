#!/usr/bin/env python3
"""M6 calibration: the default actor model, once per problem, no retries, on a spread of
HumanEval problems. If it already passes nearly all of them, Reflexion has no room to show a
gain; pick a harder benchmark or a weaker model before M7 (docs/V1_BUILD_PLAN.md M6).

Model calls go through the model_call sub-chain in RuleGo (budgeted, traced); completions run
only in the sandbox. The default model, claude-code-haiku, is `claude -p` on the logged-in
Claude Code session (subscription usage, unpriced), so RuleGo must run where `claude` is
logged in (docs/NOTES.md). Tokens and the model that answered are recorded per problem and,
as observed metrics, in the run's session context. Needs DATABASE_URL, RULEGO_URL and the
sandbox image.

    python bench/calibrate.py --n 20
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench import humaneval  # noqa: E402
from kernel import Kernel  # noqa: E402
from modules.evaluation.models import load_catalog, record_spend, use_model  # noqa: E402
from modules.systems.runs import record_run, start_run  # noqa: E402
from pclib import chains as c  # noqa: E402
from pclib import data_root  # noqa: E402
from pclib.rulego import ChainFailed, RuleGo  # noqa: E402

CALLER = "calibrate_caller"


def calibrate(k: Kernel, rg: RuleGo, model: str, n: int, budget_usd: float, catalog=None,
              dataset: Path | None = None) -> dict:
    by = k.ensure_node("agent", "urn:pc:agent:calibration", "M6 calibration")
    k.load_module("evaluation")
    load_catalog(k, *([catalog] if catalog else []), by=by)
    rg.deploy_paths(ROOT / "modules/evaluation/chains")
    rg.deploy(c.chain(CALLER, root=True, nodes=[c.subchain("ask", "model_call")]))
    system = k.create_context("system", f"calibration: {model} on HumanEval, one try")
    use_model(k, system, "actor", model, by=by)
    run = start_run(k, system, budget_usd=budget_usd, by=by,
                    conditions={"purpose": "calibration", "benchmark": "humaneval", "n": n, "model": model})
    results = []
    for p in humaneval.spread(humaneval.problems(dataset), n):
        tid = humaneval.task_id(p)
        ask = {"model_role": "actor",
               "messages": [{"role": "user", "content": humaneval.PROMPT.format(prompt=p["prompt"])}]}
        try:
            reply = rg.execute(CALLER, ask, {"run_id": run, "task_id": tid, "episode": 0, "call": "solve"},
                               timeout=600)
        except ChainFailed as e:
            if "refusing model call" in e.body:          # budget spent: stop, report what ran
                break
            raise
        work = data_root() / "runs" / run / "tasks" / tid / "ep-0" / "work"
        r = humaneval.check(p, humaneval.extract_code(reply["text"]), work)
        results.append({"task": p["task_id"], "passed": r["passed"], "timed_out": r["timed_out"],
                        "served_model": reply["model"], "usage": reply["usage"], "cost_usd": reply.get("cost_usd")})
    metrics = {k.get(a).args["name"]: k.get(a).value for a in record_spend(k, run, record_run(k, run, by=by), by=by)}
    passed = sum(r["passed"] for r in results)
    summary = {"run_id": run, "model": model, "attempted": len(results), "requested": n, "passed": passed,
               "pass_rate": round(passed / len(results), 3) if results else None,
               "served_models": metrics.get("llm_served_models", {}),
               "tokens": {kind: metrics.get(f"llm_{kind}_tokens", 0)
                          for kind in ("input", "output", "cache_read", "cache_write")},
               "cost_usd": metrics.get("llm_spend_usd", 0), "results": results}
    out = data_root() / "runs" / run / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", default="claude-code-haiku", help="catalog id of the actor model")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--budget", type=float, default=3.0,
                    help="USD; model_call refuses calls past it (unpriced models never reach it)")
    a = ap.parse_args()
    k = Kernel.connect()
    try:
        s = calibrate(k, RuleGo(), a.model, a.n, a.budget)
    finally:
        k.conn.close()
    print(json.dumps({key: v for key, v in s.items() if key != "results"}, indent=2))
    for r in s["results"]:
        u = r["usage"]
        print(f"{'PASS' if r['passed'] else 'FAIL'} {r['task']:<14} {r['served_model']:<22} "
              f"in {u['input_tokens']} out {u['output_tokens']} cache r/w "
              f"{u['cache_read_input_tokens']}/{u['cache_creation_input_tokens']}")


if __name__ == "__main__":
    main()

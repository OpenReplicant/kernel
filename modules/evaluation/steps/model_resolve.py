"""model_call, step 1 of 3 (resolve): which model, endpoint and parameters this call uses,
read from the kernel for the run's system and the caller's model role. Refuses the call when
the run's budget is spent, and traces the request with the full prompt.

In:  {"model_role", "messages", "system"?, "params"?, "slot"?}; meta.call names the call
     (it scopes the sub-chain's step keys, so two calls in one iteration don't collide).
Out: {"call": everything invoke needs, plus prices for account}
"""
from kernel import Kernel
from modules.evaluation.models import BudgetExceeded, resolve
from pclib import connect, data_root, run_step, trace


def main(inp):
    if not inp.meta.get("call"):
        raise ValueError("set meta.call (e.g. the calling slot) before entering model_call")
    d = inp.data
    with connect() as conn:
        system, budget, spent = conn.execute(
            "select system, budget_usd, spent_usd from run.run where id = %s", (inp.run_id,)).fetchone()
    if spent >= budget:
        raise BudgetExceeded(f"run {inp.run_id} has spent ${spent} of its ${budget} budget; "
                             f"refusing model call {inp.meta['call']!r}")
    kernel = Kernel.connect(data_root=data_root())
    try:
        cfg = resolve(kernel, system, d["model_role"], d.get("params"))
    finally:
        kernel.conn.close()
    slot = d.get("slot") or inp.meta["call"]
    call = {**cfg, "system": d.get("system"), "messages": d["messages"],
            "model_role": d["model_role"], "slot": slot}
    trace("llm.request", slot=slot, payload={
        "model": cfg["model_id"], "model_role": d["model_role"], "endpoint": cfg["endpoint"],
        "params": cfg["params"], "system": d.get("system"), "messages": d["messages"]})
    return {"data": {"call": call}}


if __name__ == "__main__":
    run_step(main)

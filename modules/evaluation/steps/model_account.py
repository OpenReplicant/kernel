"""model_call, step 3 of 3 (account): prices the reply, adds the cost to the run's spend
(pclib commits it with this step, exactly once), traces the response, and ends the call.

In:  {"call", "reply"}.   Out: {"text", "stop_reason", "model", "usage", "cost_usd"}; meta.call
is cleared so the caller's next steps are keyed as before.
"""
from modules.evaluation.models import cost_usd
from pclib import add_usage, run_step, trace


def main(inp):
    call, reply = inp.data["call"], inp.data["reply"]
    cost = cost_usd(call["prices"], reply["usage"])
    add_usage(calls=1, cost_usd=cost, **reply["usage"])
    trace("llm.response", slot=call["slot"], payload={
        "model": reply["model"], "model_role": call["model_role"], "text": reply["text"],
        "stop_reason": reply["stop_reason"], "usage": reply["usage"], "cost_usd": cost})
    return {"data": {"text": reply["text"], "stop_reason": reply["stop_reason"], "model": reply["model"],
                     "usage": reply["usage"], "cost_usd": cost},
            "meta": {"call": ""}}


if __name__ == "__main__":
    run_step(main)

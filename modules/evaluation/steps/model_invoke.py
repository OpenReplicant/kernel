"""model_call, step 2 of 3 (invoke): calls the endpoint resolve chose. The step's stored
output makes a rerun of the run replay the reply instead of calling again.

In:  {"call"} from resolve.   Out: {"call", "reply": {text, stop_reason, model, usage}}
"""
from modules.evaluation.providers import call as call_endpoint
from pclib import run_step


def main(inp):
    call = inp.data["call"]
    return {"data": {"call": call, "reply": call_endpoint(call)}}


if __name__ == "__main__":
    run_step(main)

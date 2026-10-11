#!/usr/bin/env python3
"""Chains the evaluation module ships, built with pclib.chains. Run to regenerate the JSON
and node stubs next to this file (a test checks they are current):

    python -m modules.evaluation.chains.build
"""
from pathlib import Path

from pclib import chains as c

HERE = Path(__file__).resolve().parent
STEPS = "modules/evaluation/steps"


def model_call(chain_id: str = "model_call") -> dict:
    """The model_call sub-chain: resolve -> invoke -> account. A caller sets meta.call, then
    reaches it with c.subchain(node_id, "model_call"); its output message is the reply."""
    return c.chain(chain_id, nodes=[
        c.step("resolve", f"{STEPS}/model_resolve.py", timeout=60),
        c.step("invoke", f"{STEPS}/model_invoke.py", timeout=900),
        c.step("account", f"{STEPS}/model_account.py", timeout=60),
    ], edges=[("resolve", "invoke"), ("invoke", "account")])


CHAINS = [model_call()]

if __name__ == "__main__":
    for ch in CHAINS:
        print(c.write(ch, HERE))

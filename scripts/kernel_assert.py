#!/usr/bin/env python3
"""Write one assertion, its arguments and evidence. Schemas: schemas/kernel_assert.*"""
from _tool import ref, run_tool, time


def main(k, inp):
    evidence = [e if isinstance(e, str) else k.add_span(e["sha256"], e["locator"], e.get("excerpt"))
                for e in inp.get("evidence", [])]
    args = {name: ref(a["node"]) if "node" in a else a["value"] for name, a in inp.get("args", {}).items()}
    if "object" in inp:
        target = {"object": ref(inp["object"])}
    else:
        target = {"value": inp["value"]}
    aid = k.assert_(ref(inp["subject"]), ref(inp["predicate"]), **target, context=ref(inp["context"]),
                    method=inp["method"], confidence=inp["confidence"],
                    asserted_by=ref(inp["asserted_by"]), evidence=evidence, args=args,
                    valid_from=time(inp.get("valid_from")), valid_to=time(inp.get("valid_to")),
                    status=inp.get("status", "accepted"))
    return {"id": aid}


if __name__ == "__main__":
    run_tool("kernel_assert", main)

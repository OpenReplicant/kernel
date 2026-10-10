#!/usr/bin/env python3
"""Query assertions. Schemas: schemas/kernel_query.*"""
from _tool import ref, run_tool, time


def main(k, inp):
    rows = k.query(subject=ref(inp["subject"]) if "subject" in inp else None,
                   predicate=ref(inp["predicate"]) if "predicate" in inp else None,
                   object=ref(inp["object"]) if "object" in inp else None,
                   context=ref(inp["context"]) if "context" in inp else None,
                   include_subcontexts=inp.get("include_subcontexts", True),
                   status=inp.get("status", ["accepted"]), method=inp.get("method"),
                   valid_at=time(inp.get("valid_at")), known_at=time(inp.get("known_at")))
    return {"assertions": rows}


if __name__ == "__main__":
    run_tool("kernel_query", main)

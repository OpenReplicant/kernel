#!/usr/bin/env python3
"""Store a file as an immutable, content-addressed source. Schemas: schemas/kernel_put_source.*"""
from _tool import run_tool


def main(k, inp):
    sha = k.put_source(inp["file"], inp["media_type"], uri=inp.get("uri"), license=inp.get("license"))
    return {"sha256": sha, "node": k.source_node(sha)}


if __name__ == "__main__":
    run_tool("kernel_put_source", main)

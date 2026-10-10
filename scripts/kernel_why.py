#!/usr/bin/env python3
"""Provenance tree of one assertion. Schemas: schemas/kernel_why.*"""
from _tool import run_tool


def main(k, inp):
    return k.why(inp["id"], depth=inp.get("depth", 3))


if __name__ == "__main__":
    run_tool("kernel_why", main)

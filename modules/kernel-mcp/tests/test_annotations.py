"""D-128: every tool says what it does to the kernel (MCP tool annotations), so a router in front lets a reader through
and holds a writer for a person: a reader is readOnlyHint true; a writer readOnlyHint false and destructiveHint false
(the kernel never deletes, invariant 2). Every profile's tools are registered on import; no database needed."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kernel_mcp.live  # noqa: E402,F401
import kernel_mcp.memory  # noqa: E402,F401
import kernel_mcp.server as server  # noqa: E402
import kernel_mcp.substrate  # noqa: E402,F401

WRITERS = {"start_run", "submit_change_set", "finish_run", "review", "remember",
           "substrate__create_item", "substrate__say", "substrate__deprecate"}
tools = asyncio.run(server.mcp.list_tools())
missing = [t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None]
if missing:
    sys.exit(f"FAIL: tools without a read-only hint: {missing}")
wrong = [t.name for t in tools if t.annotations.read_only_hint == (t.name in WRITERS)]
if wrong:
    sys.exit(f"FAIL: tools whose hint is not what they do: {wrong}")
destructive = [t.name for t in tools if t.name in WRITERS and t.annotations.destructive_hint is not False]
if destructive:
    sys.exit(f"FAIL: writers not marked non-destructive: {destructive}")
print(f"ok: all {len(tools)} tools say whether they write; {len(WRITERS)} writers, none destructive")

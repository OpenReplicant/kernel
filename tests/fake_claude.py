#!/usr/bin/env python3
"""A stand-in for `claude -p` in tests: answers with its arguments, stdin and working directory
as the result, fixed usage, as `fake-served-model`. With `--output-format stream-json` (an agent
run) it streams an init event listing one tool per MCP server it was given, a tool call and its
result, then the answer."""
import json
import os
import sys

prompt = sys.stdin.read()
argv = sys.argv[1:]
answer = json.dumps({"argv": argv, "prompt": prompt, "cwd": os.getcwd()})
result = {
    "type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn", "num_turns": 2,
    "total_cost_usd": 0.0004, "result": answer,
    "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1200},
    "modelUsage": {"fake-served-model": {"inputTokens": 3, "outputTokens": 7, "cacheCreationInputTokens": 1200},
                   "fake-helper-model": {"inputTokens": 100, "outputTokens": 1}},
}
if argv[argv.index("--output-format") + 1] != "stream-json":
    print(json.dumps(result))
    sys.exit(0)
servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
events = [
    {"type": "system", "subtype": "init", "model": "fake-served-model",
     "tools": [f"mcp__{name}__sandbox_run" for name in servers],
     "mcp_servers": [{"name": name, "status": "connected"} for name in servers]},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Testing first."},
        {"type": "tool_use", "id": "t1", "name": "mcp__sandbox__sandbox_run", "input": {"command": ["true"]}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": '{"exit_code": 0}'}]}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
    result,
]
for e in events:
    print(json.dumps(e))

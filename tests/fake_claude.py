#!/usr/bin/env python3
"""A stand-in for `claude -p ... --output-format json` in tests: answers with its arguments,
stdin and working directory as the result, fixed usage, as `fake-served-model`."""
import json
import os
import sys

prompt = sys.stdin.read()
print(json.dumps({
    "type": "result", "is_error": False, "stop_reason": "end_turn", "total_cost_usd": 0.0004,
    "result": json.dumps({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd()}),
    "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1200},
    "modelUsage": {"fake-served-model": {"inputTokens": 3, "outputTokens": 7, "cacheCreationInputTokens": 1200},
                   "fake-helper-model": {"inputTokens": 100, "outputTokens": 1}},
}))

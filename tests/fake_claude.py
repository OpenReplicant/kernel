#!/usr/bin/env python3
"""A stand-in for `claude -p ... --output-format json` in tests: echoes its arguments and
stdin back as the result, with fixed usage, answering as `fake-served-model`."""
import json
import os
import sys

prompt = sys.stdin.read()
argv = sys.argv[1:]
print(json.dumps({
    "type": "result", "is_error": "FAIL" in prompt, "stop_reason": "end_turn",
    "result": "usage limit reached" if "FAIL" in prompt else json.dumps({"argv": argv, "prompt": prompt, "cwd": os.getcwd()}),
    "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1200},
    "modelUsage": {"fake-served-model": {"inputTokens": 3, "outputTokens": 7}},
}))

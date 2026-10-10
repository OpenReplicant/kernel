"""Edge cases: exit code, sleep, environment, argv size, stdout/stderr behaviour."""
import json
import os
import sys
import time

env = json.loads(sys.argv[1])
d = env["data"]
time.sleep(d.get("sleep", 0))
if d.get("stderr_only"):
    print("only stderr", file=sys.stderr)
    sys.exit(0)
d["probe"] = {"cwd": os.getcwd(), "has_database_url": "DATABASE_URL" in os.environ,
              "argv_len": len(sys.argv[1]), "stdin_isatty": sys.stdin.isatty(),
              "stdin": sys.stdin.read() if not sys.stdin.isatty() else None}
sys.stdout.write(json.dumps(env))
sys.exit(d.get("exit", 0))

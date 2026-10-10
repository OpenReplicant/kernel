"""Toy step plumbing for the M0 spike: the message envelope arrives as argv[1] (RuleGo's
exec node passes ${data}; it has no stdin), and the updated envelope goes to stdout."""
import json
import sys


def step(fn):
    env = json.loads(sys.argv[1])
    print(f"{sys.argv[0]} got {env}", file=sys.stderr)      # stderr is dropped by exec
    fn(env["data"])
    env["data"].setdefault("trail", []).append(sys.argv[0].rsplit("/", 1)[-1][:-3])
    sys.stdout.write(json.dumps(env))

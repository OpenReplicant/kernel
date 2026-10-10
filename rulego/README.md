# rulego/: the runtime build

Everything that decides what our RuleGo-Server is: how it's built, what's compiled into it,
how it's configured. Chains themselves live with whatever they belong to (a paper's
`papers/<id>/chains/`, platform chains later) and are deployed over the REST API.

```
rulego/
├── Containerfile     server binary (from server/) on a Python base, repo mounted at /repo
├── config.conf       server config: data dir, port, command whitelist, logging, MCP off
├── server/           our Go module: upstream RuleGo-Server v0.38.0 + what we compile in
│   ├── main.go       upstream's entry point, kept minimal
│   ├── components.go extra components, one blank import each, with the reason
│   └── go.mod/go.sum pinned versions
└── spike/            M0 evidence: exec/ (stock exec node) and pclib/ (the node we use)
```

## The step node

Steps are Python scripts defining `main(inp)` (docs/SCRIPT_CONTRACT.md). In chains they run
on the `x/python` node (from rulego-components) through **pclib**: `pclib.chains` builds
the chain JSON and writes one stub per step node,

```python
from pclib.node import bind
Process = bind("/repo/registry/reflect_verbal.py", "reflector", timeout=300)
```

so the node knows its script and slot, and `x/python` pre-imports pclib, psycopg and the
step module when it starts a worker. Message data is the step's data; `run_id`, `task_id`
and `episode` travel in metadata. Build chains with `pclib.chains`, never by hand.

## Customizing the build

| Want | Where |
|---|---|
| Another upstream component (e.g. OTel export: `rulego-components/external/otel`) | a blank import in `server/components.go`, then `go mod tidy` in `server/` |
| An aspect on every node (tracing, budgets) | register it in `server/` (RuleGo aspects are Go, registered at engine creation); not needed yet, pclib does this |
| Our own Go component | `server/` too, only with a reason a Python step can't serve |
| Podman access for sandboxes (M5) | install `podman-remote` in `Containerfile`; mount the user's rootless socket in `compose.yml` |
| Server settings | `config.conf` (every key also has a `RULEGO_*` env override) |

Bump RuleGo by changing the versions in `server/go.mod` (`go get github.com/rulego/rulego/server@<commit>`),
rebuilding, and running the smoke tests.

## Build and check

```sh
podman build -f rulego/Containerfile -t localhost/kernel-rulego .
podman compose up -d postgres rulego
RULEGO_URL=http://127.0.0.1:9090 RULEGO_DATABASE_URL=$DATABASE_URL .venv/bin/pytest tests/smoke
```

If Docker Hub rate-limits you, pass mirrors: `--build-arg GO_IMAGE=mirror.gcr.io/library/golang:1.25
--build-arg PY_IMAGE=mirror.gcr.io/library/python:3.12-slim`.

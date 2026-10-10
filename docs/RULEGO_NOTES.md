# RuleGo notes (M0)

Verified on **RuleGo v0.38.0** (rulego repo tag, commit `13787639c406`) plus the `x/python`
node from rulego-components (commit `fbbef88`), built as our own server module
(`rulego/server`, see `rulego/README.md`). Sources read: the rulego repo at that tag
(`components/action/exec_node.go`, `components/common/while_node.go`, `server/`),
rulego-components (`action/python`, `pkg/python_engine`), and the `rulego-doc` English docs.
Every behaviour below was tried against a running container; the chains are in
`rulego/spike/`, the checks in `tests/smoke/test_rulego_spike.py`.

## Decisions

| Question | Decision |
|---|---|
| Artifact | Build our own image, `rulego/Containerfile`: our server module (upstream + `x/python`) on a Python base. No official image exists; upstream's Dockerfile is Alpine without Python |
| Where it runs | In a container (compose service `rulego`), repo mounted read-only at `/repo`, data root at `/data` |
| How steps run | **`x/python` node** running a generated stub (`Process = pclib.node.bind(script, slot, timeout)`); chains built with `pclib.chains`. The stock `exec` node works too (section 2) but is a worse fit |
| Agent loops | A `while` node whose `do` is `chain:<iteration chain>`; each iteration is an acyclic chain of slot steps |
| Sub-systems | `flow` node (`targetId`, `extend: true`) |
| Branching | `switch` node on message fields (`msg.data.passed == true`) |
| Deploying chains | REST, hot: `scripts/rulego_deploy.py <file or dir>` |
| Calling a chain | `POST /api/v1/rules/{id}/execute/{msgType}`, synchronous, JSON body = the envelope |
| Custom Go | None: one upstream component compiled in (`rulego/server/components.go`) |

## 1. Distribution

- The standalone `rulego/rulego-server` repo stopped in April 2026; the server now lives in
  the main repo under `server/` (its own Go module, Go 1.25). Docs at rulego.cc describe the
  newer server; some keys (e.g. `msg_max_hops`) exist only after v0.38.0.
- Config keys used (`rulego/config.conf`): `data_dir`, `server`, `cmd_white_list = python3`,
  `debug`, `run_log_mode`, `script_max_execution_time` (JS/expr only), `require_auth`, `[mcp]`.
  Each also has an env override (`RULEGO_*`).
- Chains load from `data/workflows/<user>/rules/*.json` at start (file name = chain id), or
  hot through `POST /api/v1/rules/{id}` with the chain JSON. We use the API.
- Synchronous execution: `POST /api/v1/rules/{id}/execute/{msgType}`; the response body is the
  final message data. A chain failure returns **HTTP 400** with the error text as body.
  `.../notify/{msgType}` is the asynchronous form (returns at once, no result).

## 2. The `x/python` node (chosen)

From rulego-components (`action/python`), compiled in through `rulego/server/components.go`.
Config: `script` (a `.py` path, or an inline function body), `timeout` (e.g. `"305s"`),
`maxRunning`, `pythonPath`.

| Behaviour | Finding | Consequence |
|---|---|---|
| Call | `Process(msg, metadata, msgType, dataType[, vars, globals])`; returns `{"msg", "metadata", "msgType"}` | `pclib.node.bind` returns that function |
| Transport | JSON over the worker's stdin/stdout; a 500 KB message passed | No argument-size limit on this path |
| Workers | Pre-started; **a file script is imported at pre-start**, then each process serves one call and exits | Imports are warm (psycopg alone is 245 ms); no state leaks between steps |
| Inline scripts | The body runs inside `Process`, so its imports happen per call | We use generated stub files, not inline bodies |
| Pool size | `maxRunning` workers; idle pools shrink to `max(1, maxRunning/3)` | Default 2 per node; ~20 MB per warm worker |
| Node `vars` | **Overridden by chain-level vars**, so a node can't name its slot through them | The stub names the script and slot |
| Metadata | Flat string map, in and out; HTTP query parameters become metadata | `run_id`, `task_id`, `episode` and step meta travel there |
| Errors | An exception fails the node; the caller gets HTTP 400 with the Python traceback | `pclib.rulego.ChainFailed` |
| Timeout | Enforced (`script execution timed out after 3s`) | pclib's own timeout is set 5 s shorter, so it reports the cause |
| Speed | 13 DB-backed, idempotent steps in ~1.05 s end to end (~75 ms each, connect and commit included) | Fine for v1 |

## 2b. The stock `exec` node (evaluated, not used)

Config: `cmd`, `args` (each supports `${data}`, `${msg.x}`, `${metadata.x}`), `log`,
`replaceData`. Security: only commands in `cmd_white_list` run.

| Behaviour | Finding | Consequence |
|---|---|---|
| stdin | **None** (empty, not a TTY) | The envelope goes in as the last argument, `${data}`; `pclib` accepts it there |
| Argument splitting | A template arg is split on spaces in the *template*, not the value; no shell | `${data}` arrives as one argv element; quotes, `$(...)`, `${...}` in values stay literal |
| Argument size | One argument over 128 KiB fails: `argument list too long` | `pclib` refuses envelopes over 120 KB before storing; large content (prompts, code, logs) is stored and passed by reference |
| stdout | Replaces `msg.Data` when `replaceData: true` | `pclib` prints the next envelope |
| Empty stdout | **stderr becomes the message data** | Steps must always print; `pclib` always does on success |
| Non-zero exit | Failure relation; caller gets HTTP 400 `exit status N`; stdout dropped | Exit 75 is still distinguishable by the caller |
| stderr | Discarded unless `log: true` | Diagnostics also go to `run.trace` / run logs, not only stderr |
| Environment | Inherits the server's (e.g. `DATABASE_URL`) | Secrets come from the container env |
| Working dir | Server's cwd, or metadata `workDir` | Scripts use absolute paths |
| Timeout | **None** in the node (a 75 s step ran to completion) | `pclib --timeout S` per step; the HTTP caller's deadline bounds the chain |
| Caller disconnects | Running child is killed within a second | A run driver that times out leaves no orphans |
| Start-up cost | ~25–30 ms per Python step without a database (13 steps in 0.38 s) | |

## 3. Agent loops

| Mechanism | Works? | Notes |
|---|---|---|
| `while` + sub-chain | **Yes, chosen** | `{"condition": "msg.data.stop != true", "do": "chain:<id>", "mode": 2}`. Each iteration's output is the next one's input; mode 2 returns the last iteration. `_loopIndex` is set in metadata; metadata `_break=true` stops early. Iterative, not recursive |
| Self-recursion via `flow` | Yes | A chain whose `flow` node targets itself; depth 1000 worked, linear (~27 ms/level). Kept for recursive designs (e.g. decomposition) |
| Re-enqueue via `notify` | Not used | Fresh execution each time, but the result can't return to the original caller |

Sub-chains (`flow`): with `extend: true` the sub-chain's output and relation continue in the
parent; with `false`, outputs of all its end branches are merged into an array.

So an agent is: a root chain with a `while` over an **iteration chain** (context builder →
policy → evaluator → … → controller), each slot a separate pclib step. The controller's
output sets the field the `while` condition reads. The iteration chain stays a DAG.

Inside a `while`, metadata `_loopIndex` numbers the iterations; pclib adds it to step keys
(`task=T1/ep=1/i=2/inc`) so a step repeated across iterations is stored once per iteration.
It stays in metadata after the loop ends, so a step after the loop sees the last index.

## 4. Rule-engine AOP

Aspects (Before/After/Around/Start/End/Completed, OnCreated, OnReload, OnDestroy) apply to
every node in a chain, but are **Go types registered when the engine is created**; the server
config can't add them, but our server module (`rulego/server`) can register them. For now
tracing, checkpoints and budget checks stay in `pclib`. (Backlog item stands.)

## 5. Conditional routing

`switch` with `cases: [{case: <expr>, then: <relation>}]`; unmatched messages take `Default`.
Expressions see `msg` (parsed JSON data), `metadata`, `id`, `ts`, `type`, `dataType`.
`jsFilter` / `exprFilter` exist too; `switch` is enough.

## 6. RuleGo and Podman

RuleGo runs in a container. Payload sandboxes (M5) are started by step scripts, so the
`rulego` container needs the user's **rootless** Podman socket mounted
(`$XDG_RUNTIME_DIR/podman/podman.sock`) and the `podman-remote` client; added in M5. Never a
rootful socket.

## 7. MCP (documented, not yet exercised; not used in v1)

- **Server:** RuleGo-Server's `[mcp]` module (`enable = true`, users need an apiKey) serves
  management APIs as MCP tools at `/api/v1/mcp/{apiKey}`, and selected chains or components
  per group at `/api/v1/mcp/{apiKey}/group/{groupName}`. Our config keeps it off.
- **Client:** `ai/mcpClient` (rulego-components-ai, v0.36.0+) calls a remote MCP tool (HTTP or
  stdio server) as a chain node: `server`, `toolName`, `args` template; the result replaces
  `msg.Data`. Compiling it in means adding `rulego-components-ai/...` to `rulego/server/components.go`.

This makes platform tools (search the registry, validate a spec, run a chain, query evidence)
a matter of exposing chains, and lets a step's implementation be any MCP tool.

## Building here vs. on a workstation

`podman build -f rulego/Containerfile -t localhost/kernel-rulego .` is the normal path. Base
images are build args (`GO_IMAGE`, `PY_IMAGE`) so a mirror can stand in when Docker Hub
rate-limits (e.g. `mirror.gcr.io/library/python:3.12-slim`). This spike ran under Docker,
not Podman: the cloud container had no Podman.

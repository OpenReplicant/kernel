# RuleGo notes (M0)

Verified on **RuleGo v0.38.0** (rulego repo tag, commit `13787639c406`), server built from
`server/cmd/server` with standard components only. Sources read: the rulego repo at that tag
(`components/action/exec_node.go`, `components/common/while_node.go`, `server/config`), and
the `rulego-doc` repo's English docs. Every behaviour below was tried against a running
container; the toy chains are in `rulego/spike/`, the checks in `tests/smoke/test_rulego_spike.py`.

## Decisions

| Question | Decision |
|---|---|
| Artifact | Build our own image, `rulego/Containerfile`: the server binary on a Python base, so `exec` nodes can run this repo's scripts. No official image exists; upstream's Dockerfile is Alpine without Python |
| Where it runs | In a container (compose service `rulego`), repo mounted read-only at `/repo`, data root at `/data` |
| How steps run | Stock `exec` node: `python3 /repo/<script>.py --step <slot id> [--timeout S] ${data}` |
| Agent loops | A `while` node whose `do` is `chain:<iteration chain>`; each iteration is an acyclic chain of slot steps |
| Sub-systems | `flow` node (`targetId`, `extend: true`) |
| Branching | `switch` node on message fields (`msg.data.passed == true`) |
| Deploying chains | REST, hot: `scripts/rulego_deploy.py <file or dir>` |
| Calling a chain | `POST /api/v1/rules/{id}/execute/{msgType}`, synchronous, JSON body = the envelope |
| Custom Go | None needed |

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

## 2. The `exec` node

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
| Start-up cost | ~25–30 ms per Python step (13 steps in 0.38 s) | Fine for v1; the long-running service fallback is not needed |

## 3. Agent loops

| Mechanism | Works? | Notes |
|---|---|---|
| `while` + sub-chain | **Yes, chosen** | `{"condition": "msg.data.stop != true", "do": "chain:<id>", "mode": 2}`. Each iteration's output is the next one's input; mode 2 returns the last iteration. `_loopIndex` is set in metadata; metadata `_break=true` stops early. Iterative, not recursive |
| Self-recursion via `flow` | Yes | A chain whose `flow` node targets itself; depth 1000 worked, linear (~27 ms/level). Kept for recursive designs (e.g. decomposition) |
| Re-enqueue via `notify` | Not used | Fresh execution each time, but the result can't return to the original caller |

Sub-chains (`flow`): with `extend: true` the sub-chain's output and relation continue in the
parent; with `false`, outputs of all its end branches are merged into an array.

So an agent is: a root chain with a `while` over an **iteration chain** (context builder →
policy → evaluator → … → controller), each slot a separate `exec` step. The controller's
output sets the field the `while` condition reads. The iteration chain stays a DAG.

## 4. Rule-engine AOP

Aspects (Before/After/Around/Start/End/Completed, OnCreated, OnReload, OnDestroy) apply to
every node in a chain, but are **Go types registered when the engine is created**; the server
config can't add them. With stock RuleGo only, tracing, checkpoints and budget checks stay in
`pclib`. (Backlog item stands.)

## 5. Conditional routing

`switch` with `cases: [{case: <expr>, then: <relation>}]`; unmatched messages take `Default`.
Expressions see `msg` (parsed JSON data), `metadata`, `id`, `ts`, `type`, `dataType`.
`jsFilter` / `exprFilter` exist too; `switch` is enough.

## 6. RuleGo and Podman

RuleGo runs in a container. Payload sandboxes (M5) are started by step scripts, so the
`rulego` container needs the user's **rootless** Podman socket mounted
(`$XDG_RUNTIME_DIR/podman/podman.sock`) and the `podman-remote` client; added in M5. Never a
rootful socket.

## Building here vs. on a workstation

`podman build -f rulego/Containerfile -t localhost/kernel-rulego .` is the normal path. Base
images are build args (`GO_IMAGE`, `PY_IMAGE`) so a mirror can stand in when Docker Hub
rate-limits (e.g. `mirror.gcr.io/library/python:3.12-slim`). This spike ran under Docker,
not Podman: the cloud container had no Podman.

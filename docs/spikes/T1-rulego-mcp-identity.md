# T1 spike: does RuleGo-Server's MCP mode pass the caller's identity into a chain?

**Answer: no.** Recorded as D-21 (closes O-7). Date: 2026-10-16.

## What was tested

- RuleGo-Server **v0.38.0**, which is the `server/` module of `github.com/rulego/rulego`, tag `v0.38.0`
  (tag object `f6b25c6`, commit `1378763`). There is no separate GitHub repository and no image on
  Docker Hub; gitee.com was unreachable from the test machine. Built from source with Go 1.25.0
  (`go build ./cmd/server`, default build tags), MCP via `github.com/mark3labs/mcp-go v0.44.0`.
- Config: `require_auth = true`, three users with API keys (`admin`, `alice`, `bob`), MCP enabled,
  one group `tools = chains,whoami`.
- Chain `whoami` in each of `data/workflows/alice/rules/` and `data/workflows/bob/rules/`: one
  `jsTransform` node returning everything it receives (`msg`, `metadata`, `msgType`) plus a
  hard-coded owner name.
- Client: a minimal MCP streamable-HTTP client (initialize → `tools/list` → `tools/call`), sending
  extra headers `Authorization: Bearer CALLER-JWT-ALICE` and `X-Caller: alice-agent`.

## Results

| Probe | Result |
| --- | --- |
| `tools/call whoami` as alice via `/api/v1/mcp/{aliceKey}/group/tools` | `{"chainOwner":"alice","metadata":{},"msgType":"","received":{"in":{"q":1}}}`: metadata empty; no username, token or header reached the chain |
| Same as bob | runs **bob's** copy of the chain (`chainOwner: bob`) |
| Unknown key in the path | HTTP 401 |
| A kernel JWT as `Authorization` with no RuleGo key | HTTP 401: the bearer must be a RuleGo key or a RuleGo-issued JWT |
| `tools/list` on the default endpoint `/api/v1/mcp/{aliceKey}` | `save_rule_chain`, `delete_rule_chain`, `operate_rule_chain`, `execute_rule_chain`, `preview_rule_chain`, `get_rule_chain`, `list_rule_chains`, `list_components`, `get_component_doc`, `list_node_pool` and `whoami` |
| Group `tools = chains` (no ids) | no tools; chains must be listed by id |

## Why (source)

`server/internal/modules/mcp/module.go`, `ruleChainToolHandler`: the handler takes the username
from the request context only to choose that user's rule engine, then calls
`ruleEngine.OnMsgAndWait(types.NewMsgWithJsonData(msg), …)`, where `msg` is the tool arguments. No
metadata is set and the request context is not passed. The handler is identical on `main` at
`fcb123d` (2026-10-16).

## Consequences

1. **Agent-facing kernel tools stay in `kernel-mcp`**, which forwards each caller's bearer token
   (D-20). RuleGo runs system chains, each under its module's identity (its `runs_as` JWT).
2. RuleGo identity is a **namespace per RuleGo user**, not a per-call identity. Mapping each agent
   to its own RuleGo user, with its own copy of every chain holding that agent's kernel JWT, would
   work but duplicates chains and stores agent tokens in RuleGo. Rejected.
3. **The default MCP endpoint cannot be limited by config**: any RuleGo key holder gets the chain
   editing tools there. The deployment must not route `/api/v1/mcp/{key}` (only
   `/api/v1/mcp/{key}/group/{allow-listed group}`, if MCP is exposed at all). The API key also travels
   in the URL path, so Caddy must not log paths for these routes. This adds to `deploy/rulego/README.md`.
4. A patch that puts the caller's username (and an opt-in forwarded header) into message metadata
   is about ten lines in `ruleChainToolHandler`. It could be offered upstream. That would reopen the
   question; it doesn't change milestone 1.
5. No published image exists. T12 needs RuleGo-Server built from the pinned tag with a Dockerfile
   in `deploy/rulego/` (golang 1.25 builder, pinned).

The eval claim this spike should also produce (`deploy/rulego/README.md`) can only be submitted
once T2–T5 give a running kernel and a submitting agent; it is carried as a T6 follow-up.

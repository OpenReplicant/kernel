# RuleGo-Server settings (chain-runner slot, alternative runner)

Put RuleGo-Server's config file in this folder; it is mounted read-only at `/config`. What we have
checked (T1, T10; docs/spikes/): RuleGo-Server is the `server/` module of github.com/rulego/rulego,
pinned at **v0.38.0**, built from source with the `with_extend` build tag (needed for the `x/python`
node, D-33); there is no published image. The config is an ini file (`config.conf`): `require_auth`,
`jwt_secret_key`, `[users]` (`name = password,apiKey`), `[mcp]` and `[mcp.groups]`, and `[global]`
values that chains read as `${global.X}`. The chain decoder's output expects `KK_API` and one
`KK_TOKEN_<AGENT>` per `runs_as` agent in `[global]`, and reads chains from
`<data_dir>/workflows/kk/rules/`. Whatever the syntax, the deployment must satisfy these rules:

| Rule | Why | Setting area (check current docs) |
| --- | --- | --- |
| Authentication on (`require_auth`), strong `jwt_secret_key` | The editor and HTTP API can change chains | auth / JWT |
| Editor and chain-editing API not routed through Caddy, not reachable by agents | Chains change only through the review lane and the chain decoder | resource mapping, network |
| Chain folder is written only by `chain-decoder-rulego` | Approved chain claims are the source of truth; drift is detected by hash | `data_dir` → the `rulego-chains` volume |
| MCP mode exposes an allow-list of chains only; components and raw APIs excluded | No raw `cmd`, HTTP or component tools for agents | `[mcp]` include / exclude |
| Route only `/api/v1/mcp/{key}/group/{group}`, never the default `/api/v1/mcp/{key}`; don't log these paths | The default endpoint always exposes chain editing tools; the key travels in the path (D-21) | Caddy routes and log config |
| `cmd` component whitelist limited to the Python interpreter and the scripts folder | Python steps run as `cmd` nodes | `cmd_white_list` |
| Every chain calls PostgREST with the JWT of its `runs_as` agent | Attribution and clearance stay per agent | chain variables / secrets |

## Milestone 1 spike (T1, done: D-21)

Prove or disprove that, when an agent calls a RuleGo chain through RuleGo-Server's MCP mode, the
**caller's** identity reaches the chain, so the chain can act with the caller's database role.

- If yes: agent-facing kernel tools can be RuleGo chains, and `kernel-mcp` stays a fallback.
- If no: agent-facing tools stay in `kernel-mcp` (it forwards each caller's bearer token), and
  RuleGo runs system chains, each with its own module identity.

Record the result as a decision in docs/decisions.md and as an eval claim.

## Observability

rulego-components includes an OpenTelemetry client node (`x/otelClient`). Per-node tracing of
every chain is not automatic; the self-telemetry slot decides how runs become traces. Chain runs
are always recorded in the kernel (one run per execution, `plan_step` per step) regardless.

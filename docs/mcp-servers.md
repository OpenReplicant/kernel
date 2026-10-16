# MCP servers in the default distribution

These are the tool servers agents get when they work **inside the platform** (the tool-provider and
agent-interface slots). They are not the tools of the Claude Code session that builds this project.
Example client config: `deploy/mcp/agents.mcp.json` (Claude Code `.mcp.json` format; `${VAR}` values come
from the environment).

**Rule:** MCP servers are a convenience layer, never the enforcement layer. Clearance, attribution and
the write path are enforced by Postgres (roles, row-level security, the change-set apply function).
Any server that can write somewhere else is configured read-only, and anything that would let an agent
reach the database directly is excluded.

Researched October 2026. Re-check versions, flags and licenses before pinning.

## Default set

| Server | Slot | Use | License | Configuration notes |
| --- | --- | --- | --- | --- |
| **kernel-mcp** (this project, `modules/kernel-mcp`) | agent-interface | search, get entity card, query claims, explain, submit change set, start/finish run, list slots and modules | Apache-2.0 | Built on the official MCP Python SDK v2 (stable since Aug 2026; implements the 2026-07-28 spec and still serves 2025-era clients; `MCPServer` replaced `FastMCP`). Holds no database role: forwards the caller's bearer token to PostgREST |
| **Git** reference server (`mcp-server-git`) | tool-provider | read history, diffs and blame for the repository being modeled | MIT | Use ≥ 2026.1.14: CVE-2026-27735 let `git_add` stage paths outside the repo before that. Agents read only; commits happen through actuators |
| **Filesystem** reference server | tool-provider | read workspace files | MIT | Scope to the workspace directory only |
| **Fetch** reference server | tool-provider | read web pages (vocabulary specs, docs) | MIT | |
| **GitHub MCP server** (`github/github-mcp-server`) | tool-provider | read repos, issues, PRs and Actions runs (CI history for milestone 2) | MIT | `stdio --read-only --toolsets repos,actions`; image `ghcr.io/github/github-mcp-server`; hosted endpoint `https://api.githubcopilot.com/mcp/` also available |

## Optional (enable per deployment)

| Server | Slot | Use | License | Notes |
| --- | --- | --- | --- | --- |
| **RuleGo-Server MCP mode** | agent-interface | expose approved chains as tools | Apache-2.0 | Allow-list chains only; see deploy/rulego/README.md and the identity spike |
| **Kubernetes MCP server** (`containers/kubernetes-mcp-server`) | tool-provider | inspect pods, logs, events | Apache-2.0 | Run with `--read-only`. Changes go through the orchestrator-driver actuator, never this server. `pods_exec` is the highest-risk tool |
| **Podman MCP server** (`manusa/podman-mcp-server`) | tool-provider | inspect containers, images, logs (Podman or Docker) | Apache-2.0 | Has no read-only flag: deny `container_run`, `container_remove`, `container_stop`, `image_*` write tools in the harness's permissions, or leave it out |
| **Grafana MCP** (`grafana/mcp-grafana`) | tool-provider | query Prometheus/Loki for self-telemetry | Apache-2.0 | `--disable-write` |
| **Wikidata MCP** (Wikimedia Deutschland) | tool-provider | entity linking: map our entities to Wikidata items with `skos:exactMatch` | — | Presented at WikidataCon 2025 with a companion vector database; confirm the current endpoint and terms before use |
| **OLS MCP server** (`seandavi/ols-mcp-server`) | tool-provider | look up terms in EBI's Ontology Lookup Service (OLS4) when choosing vocabularies | not stated | Small community project; check its license before adopting |
| **pm4py-mcp** | tool-provider | process mining over OCEL logs (milestone 2) | AGPL-3.0 | Keep out of the default distribution (license posture, D-11); fine for internal analysis |

## Deliberately excluded

| Server | Why |
| --- | --- |
| Postgres MCP Pro (`crystaldba/postgres-mcp`) and other generic SQL servers | Generic SQL bypasses the change-set door and API views. Also CVE-2026-85620 (published Sept 2026): its restricted mode could be bypassed to read server files via FROM-clause function calls in versions ≤ 0.3.0 |
| The archived reference Postgres and SQLite servers | Archived by the MCP steering group; same bypass problem |
| The Memory reference server (knowledge-graph memory) | A second, ungoverned memory would compete with the kernel and skip attribution and belief |
| Sequential Thinking reference server | No role in this architecture |

## Sources

- MCP reference servers and archive list: https://github.com/modelcontextprotocol/servers
- CVE-2026-27735 (mcp-server-git): https://releasealert.dev/cve/CVE-2026-27735
- MCP Python SDK v2: https://pydantic.dev/articles/mcp-python-sdk-v2-beta
- GitHub MCP server: https://github.com/github/github-mcp-server
- Kubernetes MCP server guide: https://mcp.directory/blog/kubernetes-mcp-server-complete-guide-2026
- Podman MCP server: https://github.com/manusa/podman-mcp-server
- Grafana MCP: https://github.com/grafana/mcp-grafana
- Wikidata MCP talk (WikidataCon 2025): https://pretalx.com/wikidatacon-2025/speaker/NZZF7U
- OLS MCP server: https://github.com/seandavi/ols-mcp-server
- pm4py-mcp: https://glama.ai/mcp/servers/azizketata/pm4py-mcp
- CVE-2026-85620 (postgres-mcp): https://radar.offseq.com/threat/cve-2026-85620-incorrect-authorization-in-crystaldba-postgres-mcp-eadd139acc14f6c5
- RuleGo-Server (MCP mode, auth, cmd whitelist): https://gitee.com/rulego/rulego-server

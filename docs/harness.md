# Dropping the kernel into an agent harness

The kernel is a Postgres database with the kernel schema plus a validator. Any harness that speaks MCP
can use it; how deeply depends on what you add (D-36):

| Level | Add | You get |
| --- | --- | --- |
| 1 | the MCP server | the agent reads, cites and submits claims under its own identity |
| 2 | + the skill file | the agent knows the conventions: attribution, references, evidence types, citing belief |
| 3 | + hooks | the harness recalls on its own: identity and a session run at start, related claims with every prompt, the run finished at the end |
| 4 | + modules | encoders, belief, further processors and decoders, a UI |

## 1. A database with the kernel

Either the compose stack (`deploy/compose.yaml`: Postgres, validator, belief model, kernel-mcp over HTTP), or any
Postgres 16+ you run yourself:

```sh
psql -d kk -f kernel/schema/001_kernel.sql -f kernel/schema/002_bootstrap.sql \
            -f modules/validator-shacl/migration.sql -f deploy/postgres/040_people.sql \
            -f deploy/postgres/050_agents.sql
psql -d kk -c "alter role kk_authenticator password '<service secret>'" \
           -c "alter role agent_claude_code login password '<agent secret>'" \
           -c "alter role person_owner login password '<owner secret>'"
pip install -r modules/validator-shacl/requirements.txt
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk KK_ROLE=kkm_validator \
  PYTHONPATH=modules/validator-shacl python -m validator &
# optional, for belief scores (also run modules/belief-corroboration/migration.sql above):
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk KK_ROLE=kkm_belief \
  PYTHONPATH=modules/belief-corroboration python -m belief_corroboration &
```

Who holds which login (D-37):

| Login | Held by | Can act as |
| --- | --- | --- |
| `kk_authenticator` | services you run (validator, belief model, kernel-mcp `--http`, PostgREST) | any role granted to it, so never give it to an agent |
| `agent_claude_code` (one per agent) | that agent's harness | itself only |
| `person_owner` (one per person) | that person | itself only |

The kernel enforces clearance and attribution by the role. `scripts/smoke_kernel.sh` does all of this
in a throwaway database and checks it end to end.

## 2. The MCP server

Install once (any Python 3.12+ environment): `pip install ./modules/kernel-mcp`, or without a checkout
`uvx --from "git+https://github.com/OpenReplicant/kernel#subdirectory=modules/kernel-mcp" kernel-mcp`.
This gives two commands: `kernel-mcp` (the MCP server) and `kernel-mcp-hook` (the hooks).

stdio, one agent per harness. For Claude Code (`.mcp.json`; the same entry is in
`deploy/mcp/agents.mcp.json`):

```json
{"mcpServers": {"kernel": {
  "command": "kernel-mcp",
  "env": {"KK_DATABASE_URL": "postgresql://agent_claude_code:<agent secret>@localhost/kk"}}}}
```

Each agent gets its own login and agent IRI (pattern: `deploy/postgres/050_agents.sql`); stdio refuses
a login that can switch roles. `KK_DATABASE_URL` (or `DATABASE_URL`) names it; the `KK_` name wins so
a project's own `DATABASE_URL` does not collide. With the compose stack, Postgres is on
`localhost:5432` and the agent's password is `KK_AGENT_CLAUDE_CODE_PASSWORD` in `deploy/.env`.

Shared server for several agents: `kernel-mcp --http` with `KK_JWT_SECRET`; each request sends
`Authorization: Bearer <JWT>` whose `role` claim is the agent's role
(`python scripts/mint_jwt.py agent_claude_code`).

## 3. The skill file

Copy `modules/kernel-mcp/SKILL.md` into the harness's skills folder (Claude Code:
`.claude/skills/knowledge-kernel/SKILL.md`). It tells the agent how to cite, what to record and how.

## 4. Hooks: recall and recording without asking

`kernel-mcp-hook` implements three hooks; the Claude Code configuration is
`modules/kernel-mcp/hooks/claude-code.json` (merge its `hooks` into `.claude/settings.json`).

| Hook | Does |
| --- | --- |
| `session-start` | starts a kernel run for the session and tells the agent its identity and run id |
| `prompt` | recalls up to `KK_RECALL_LIMIT` (8) claims ranked by relevance to the prompt, with ids and belief |
| `session-end` | finishes the session's run |

Hooks inherit the harness's environment, so export `KK_DATABASE_URL` (the agent's own login) before
starting the harness. A hook never blocks the session: on any error it writes to stderr and exits 0.
Session runs are remembered in `KK_STATE_DIR` (default `~/.local/state/knowledge-kernel`). Other
harnesses call the same commands with their own hook JSON (`session_id`, `prompt`, `reason`).

## Reviewing

Typing new properties or classes goes to the review lane. A person approves with
`DATABASE_URL=postgresql://person_owner:<owner secret>@localhost/kk python scripts/review.py list`, then
`approve <ids>`.

# Dropping the kernel into an agent harness

The kernel is a Postgres database with the kernel schema plus a validator. Any harness that speaks MCP
can use it; how deeply depends on what you add (D-36):

| Level | Add | You get |
| --- | --- | --- |
| 1 | the MCP server | the agent reads, cites and submits claims under its own identity |
| 2 | + the skill file | the agent knows the conventions: attribution, references, evidence types, citing belief |
| 3 | + hooks (T15) | the harness recalls and records on its own: context at session start, the session's run and claims at the end |
| 4 | + modules | encoders, belief, further processors and decoders, a UI |

## 1. A database with the kernel

Either the compose stack (`deploy/compose.yaml`: Postgres, validator, kernel-mcp over HTTP), or any
Postgres 16+ you run yourself:

```sh
psql -d kk -f kernel/schema/001_kernel.sql -f kernel/schema/002_bootstrap.sql \
            -f modules/validator-shacl/migration.sql -f deploy/postgres/040_people.sql \
            -f deploy/postgres/050_agents.sql
psql -d kk -c "alter role kk_authenticator password '<secret>'"
pip install -r modules/validator-shacl/requirements.txt
DATABASE_URL=postgresql://kk_authenticator:<secret>@localhost/kk KK_ROLE=kkm_validator \
  PYTHONPATH=modules/validator-shacl python -m validator
```

`kk_authenticator` is a login that can only switch to the roles granted to it (each agent's own role);
the kernel enforces clearance and attribution by that role. `scripts/smoke_kernel.sh` does all of this
in a throwaway database and checks it end to end.

## 2. The MCP server

stdio, one agent per harness. For Claude Code (`.mcp.json`; the same entry is in
`deploy/mcp/agents.mcp.json`):

```json
{"mcpServers": {"kernel": {
  "command": "python", "args": ["-m", "kernel_mcp"],
  "env": {"PYTHONPATH": "/path/to/kernel/modules/kernel-mcp",
          "DATABASE_URL": "postgresql://kk_authenticator:<secret>@localhost/kk",
          "KK_ROLE": "agent_claude_code"}}}}
```

Install its dependencies once: `pip install -r modules/kernel-mcp/requirements.txt`. Each agent gets
its own role and agent IRI (pattern: `deploy/postgres/050_agents.sql`).

Shared server for several agents: `python -m kernel_mcp --http` with `KK_JWT_SECRET`; each request
sends `Authorization: Bearer <JWT>` whose `role` claim is the agent's role
(`python scripts/mint_jwt.py agent_claude_code`).

## 3. The skill file

Copy `modules/kernel-mcp/SKILL.md` into the harness's skills folder (Claude Code:
`.claude/skills/knowledge-kernel/SKILL.md`).

## Reviewing

Typing new properties or classes goes to the review lane. A person approves with
`DATABASE_URL=... KK_ROLE=person_owner python scripts/review.py list | approve <ids>`.

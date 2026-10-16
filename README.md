# Knowledge Kernel

A general-purpose engine that learns from claims and acts on them. One Postgres holds every claim,
each attributed, referenced, belief-scored and scoped. Modules plug into slots as encoders,
processors, decoders, actuators, interfaces, agents and controls, and the system describes itself in
its own model. Almost every component is existing open tooling; this repository builds the kernel,
the slot contracts and the default modules.

- **Specification:** `docs/spec.md` (kernel v0 is frozen)
- **Current work and status:** `docs/milestone-1.md`
- **Decisions and open questions:** `docs/decisions.md`
- **Working rules:** `CLAUDE.md`

## Run it locally

```sh
cp deploy/.env.example deploy/.env       # fill in passwords and KK_JWT_SECRET (>= 32 chars), never commit it
python scripts/mint_jwt.py kkm_validator # repeat for each KK_JWT_* in .env (module roles, see modules/*/migration.sql)
docker compose -f deploy/compose.yaml --env-file deploy/.env --profile modules up -d --build
```

The UI and API are on http://127.0.0.1:8080 (`/`, `/api`, `/mcp`, `/graph`). People and agents use
their own tokens:

| Who | Role | Token |
| --- | --- | --- |
| Local owner (reviews, redacts) | `person_owner` | `python scripts/mint_jwt.py person_owner` |
| Claude Code over MCP | `agent_claude_code` | the `KK_AGENT_JWT` in `deploy/mcp/agents.mcp.json` |

Module, chain and vocabulary changes wait in the review lane: `python scripts/review.py list`, then
`approve <ids>` (with `KK_TOKEN` set to the owner's token), or use the UI's review tab.

To see everything working end to end with throwaway secrets: `sh scripts/smoke_stack.sh`.

## Layout

```
kernel/    schema (frozen v0), JSON Schemas for change sets, manifests and chains, Python contracts
ontology/  kk-core, kk-tosca, kk-profile vocabularies; packs.yaml; slots.yaml (42 slots)
shapes/    SHACL shapes and fixtures
modules/   one folder per module: manifest, chains, migration, code, tests, Dockerfile
deploy/    compose stack, Postgres image and init scripts, Caddyfile, RuleGo notes, MCP client config
scripts/   tokens, manifest checks, review CLI, the stack smoke test and gate checks
eval/      eval sets
docs/      spec, milestone, decisions, MCP servers, sources, spikes
```

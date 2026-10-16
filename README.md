# Knowledge Kernel

A small knowledge kernel that drops into any agent harness. One Postgres holds every claim, each
attributed, referenced, belief-scored and scoped. Its three jobs are ingestion, processing and recall,
each done by a swappable default module; everything else is a module too. Use it over MCP alone, or
add the skill file, harness hooks and further modules (`docs/harness.md`).

- **Specification:** `docs/spec.md` (kernel v0 is frozen)
- **Current work and status:** `docs/milestone-1.md`
- **Decisions and open questions:** `docs/decisions.md`
- **Working rules:** `CLAUDE.md`

## Run it locally

In an agent harness, with your own Postgres: `docs/harness.md`. As a compose stack (Postgres, validator,
kernel-mcp over HTTP behind Caddy at http://127.0.0.1:8080/mcp):

```sh
cp deploy/.env.example deploy/.env       # fill in passwords and KK_JWT_SECRET (>= 32 chars), never commit it
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
python scripts/mint_jwt.py agent_claude_code   # the agent's bearer token for /mcp
```

| Who | Role | Identity |
| --- | --- | --- |
| Local owner (reviews, redacts) | `person_owner` | `KK_ROLE=person_owner python scripts/review.py list` |
| Claude Code over MCP | `agent_claude_code` | `KK_ROLE` (stdio) or a JWT minted for the role (HTTP) |

Module and vocabulary changes wait in the review lane: `python scripts/review.py list`, then
`approve <ids>`.

To check the kernel end to end in a throwaway database (no Docker): `sh scripts/smoke_kernel.sh`.

## Layout

```
kernel/    schema (frozen v0), JSON Schemas for change sets, manifests and chains, Python contracts
ontology/  vocabularies (being reduced to standards), packs.yaml, slots.yaml (42 slots)
shapes/    SHACL shapes and fixtures
modules/   default modules: manifest, migration, code, tests, Dockerfile each
deploy/    compose stack, Postgres image and init scripts, Caddyfile, MCP client config
scripts/   kernel smoke test, manifest checks, review CLI, tokens
parked/    modules and scripts outside the kernel (runners, decoders, UI); not maintained
eval/      eval sets
docs/      spec, milestone, decisions, MCP servers, sources, spikes
```

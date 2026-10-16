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

One command, `kk`, sets up and runs a kernel (`pip install -e .` from a checkout, Python 3.12+):

```sh
export KK_ADMIN_URL=postgresql://postgres:<password>@localhost/kk   # a superuser, for setup only
KK_AUTHENTICATOR_PASSWORD=<service secret> kk init   # kernel, default modules, vocabulary packs
kk person add owner --steward                        # your own login (prints its password once)
kk agent add claude-code                             # the agent's own login, for its harness
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk KK_JWT_SECRET=<32+ chars> \
  kk serve                                           # MCP over HTTP + validator + belief model
```

| Command | Does |
| --- | --- |
| `kk init` | installs kernel v0, every default module's migration and the vocabulary packs (once) |
| `kk person add NAME` / `kk agent add NAME` | an own login that can act only as itself (D-37) |
| `kk serve [--only mcp,validator,belief]` | the running kernel: one process, or one container per part |
| `kk status` | who you are, what you can see, what is waiting for review |
| `kk review list \| show \| approve \| reject` | the review lane, as yourself |
| `kk scan --repo DIR` | ingest a repository (ingest filter first; needs gitleaks) |
| `kk token ROLE` | a bearer token for MCP over HTTP |

Or as a compose stack (Postgres, then one image running `kk serve` behind Caddy at
http://127.0.0.1:8080/mcp):

```sh
cp deploy/.env.example deploy/.env       # fill in passwords and KK_JWT_SECRET (>= 32 chars), never commit it
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm init kk agent add claude-code
```

In an agent harness: `docs/harness.md`. To check the kernel end to end in a throwaway database (no
Docker): `sh scripts/smoke_kernel.sh`.

## Layout

```
kk/        the kk command and the module library (kk.lib)
kernel/    schema (frozen v0), JSON Schemas for change sets and manifests, Python contracts
ontology/  kk-core (the module contract), profile packs, packs.yaml, slots.yaml (42 slots)
shapes/    SHACL shapes and fixtures
modules/   default modules: manifest, migration, code, tests each
deploy/    compose stack, Postgres image, Caddyfile, MCP client config
scripts/   kernel smoke test, manifest checks
parked/    modules and scripts outside the kernel (runners, decoders, UI); not maintained
eval/      eval sets
docs/      spec, milestone, decisions, MCP servers, sources, spikes
```

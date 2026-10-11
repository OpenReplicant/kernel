# Working notes

Current state, open decisions and practical know-how, kept so a new session (or a person)
can pick up without the conversation that produced them. Update it at each milestone.

## State (2026-10-10)

| Milestone | Gate | Notes |
|---|---|---|
| M0 RuleGo | passed | RuleGo-Server v0.38.0 + `x/python`, built from `rulego/server`; findings in `docs/RULEGO_NOTES.md` |
| M1 foundation | passed | `pclib` contract; `compose.yml` written but not yet run under Podman |
| M2 kernel core | passed | Thermostat domain with sensor-log and report evidence |
| M3 vocabularies, validation, views, modules | passed | Four modules; exact `spec.yaml` round trip; kernel tests pass with no `modules/` present |
| M4 model access | not started; shape settled (below) | `docs/V1_BUILD_PLAN.md` M4 |

Test suite: 51 pass, 4 smoke tests skip unless a RuleGo container is up (`RULEGO_URL`).

## Decisions taken

- The kernel is an evidence-based world model, domain-free. Upper ontology in two parts:
  knowing (Agent, Source, produced_by, holds, reliability) and structure (is_a, part_of,
  depends_on, plays, ports, couplings). Methods: stated, observed, inferred, computed,
  defaulted; stated and observed need spans (database-enforced). Context kinds: world,
  perspective, system, session, vocabulary.
- The database enforces: assertions only change status along a fixed lifecycle, nothing in
  `kb.*` is deleted, evidence for stated/observed, node kinds, status history.
- Layers L0–L3 and modules with manifests (`docs/ARCHITECTURE.md`). `systems` is a module
  (L1), `evaluation` separate from `agent_design`.
- RuleGo runs in a container built from our own server module (`rulego/server`), so build-time
  additions have one place. Steps run on `x/python` through pclib stubs, not `exec`.
- Agent loops are a `while` over an acyclic iteration chain; recursion via `flow`.
- MCP is transport at the edges (RuleGo can serve chains as MCP tools; `ai/mcpClient` calls
  any MCP tool as a step). The agent-design rule forbids only *model-invoked* internal slots.

## Settled (2026-10-10): knowing and doing, and model access

- **Knowing and doing.** The kernel (record + belief) knows; the runtime (RuleGo, pclib,
  models, harnesses, sandbox) does, as a peer that the kernel never contains. They meet at
  three joints: describe (systems compile to chains), read (steps read facts and beliefs),
  record (every run is a source and its observations are evidenced). Applications use four
  interfaces: teach, ingest, believe, act. `docs/ARCHITECTURE.md`.
- **Belief.** `status` is today's single stored judgment; `beliefs(perspective, policy, at)`
  as a computed view comes when a second source of trust needs weighing (backlog,
  `docs/KERNEL.md` Belief).
- **M4 = models known to the kernel + a budgeted `model_call` sub-chain + runs as sources.**
  Models, endpoints, parameters and prices are assertions; a harness is an `ev:Harness`
  with its own model settings. Budget and usage live in the sub-chain around RuleGo's
  `ai/llm` (or a pclib SDK step if `ai/llm` falls short; verify first). Tracing that every
  node needs belongs in an aspect in `rulego/server` (backlog); pclib steps trace themselves
  until then. `scripts/claude_call.py` is dropped.
- **M5 adds `harness_session`**: Codex, Claude Code, … headless or through their MCP servers,
  in a container whose network reaches only the model endpoint; transcript stored as a
  source. Verify first which harnesses offer headless or MCP modes and their model, endpoint
  and spending options (from memory, unverified: `codex exec`, `codex mcp-server`,
  `claude -p`, `claude mcp serve`).
- **Alongside a harness** (backlog): serve the platform's chains to a harness over RuleGo's
  MCP endpoint; any pipeline stays replaceable by a custom chain.

## Other known gaps

- `compose.yml` hasn't been run under Podman; the `rulego` container was tested with Docker.
- Chain compilation from a system context (`couplings → chain`) is M7 work; `pclib.chains` is
  the builder it will use.
- The Reflexion example was written from memory; M6 verifies it against the paper.
- Sandbox (M5) will need the rootless Podman socket and `podman-remote` in the RuleGo image.

## Cloud-session know-how

This repo has been developed in a Claude Code cloud container. What that needed:

- **Postgres:** Postgres 16 binaries exist but no service. `tools/dev_postgres.sh start` starts a throwaway cluster as the
  `postgres` user in a directory it can reach (e.g. `/var/lib/postgresql/kscratch`), socket
  only, port 5499, trust auth; then
  `DATABASE_URL="postgresql://postgres@/postgres?host=/var/lib/postgresql/kscratch&port=5499"`.
  It doesn't survive a container restart; start it again.
- **Containers:** no Podman; `dockerd` can be started by hand. Docker Hub rate-limits, so
  use `mirror.gcr.io/library/...` base images (the Containerfile takes `GO_IMAGE`/`PY_IMAGE`).
  Builds go through a TLS-intercepting proxy: build local base images that trust
  `/root/.ccr/ca-bundle.crt`, pass them as `GO_IMAGE`/`PY_IMAGE`, and build with
  `--network host --build-arg HTTPS_PROXY=$HTTPS_PROXY`.
- **RuleGo container with the local database:** mount the socket directory,
  `-v /var/lib/postgresql/kscratch:/pgsock -e DATABASE_URL="postgresql://postgres@/<db>?host=/pgsock&port=5499"`.
- **Sources:** rulego.cc is blocked; the docs are in github.com/rulego/rulego-doc, the server
  in github.com/rulego/rulego (`server/`), components in rulego-components and
  rulego-components-ai. `git clone` and the Go module proxy work.

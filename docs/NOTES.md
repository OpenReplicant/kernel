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
| M4 model access | **not started; shape under review** | See below |

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

## Open: the shape of M4 (model access)

The plan says every model call goes through `scripts/claude_call.py` with a per-run budget.
Direction from the user (2026-10-10), to settle before building M4:

1. **RuleGo's `ai/llm` node** (rulego-components-ai, OpenAI-compatible endpoints) for plain
   model calls inside chains. Open question: budget enforcement and `llm.request`/`llm.response`
   tracing happen in pclib today; an `ai/llm` node would bypass both unless wrapped (a pclib
   step before/after it, or an aspect compiled into `rulego/server`).
2. **A generic agent-harness runner** rather than only raw model calls: a step (or node) that
   starts an agent harness session, e.g. Codex CLI or Claude Code, interactively or headless,
   gives it a task and a workspace, and records the session transcript as a source. Several
   harnesses can run as MCP servers, which would make the runner an `ai/mcpClient` call.
   **To verify:** which harnesses expose an MCP server or a scriptable session mode, and their
   options (from memory: `codex mcp-server`, Codex `exec`, `claude -p`, `claude mcp serve`).
   Sessions run in the sandbox; their transcripts are `observed` evidence.
3. **Run alongside a harness:** this whole system should work as a toolset for an agent harness
   (kernel and platform operations exposed as tool-shaped scripts, later MCP via RuleGo), and
   every pipeline should be replaceable by custom chains.

Likely consequence: M4 becomes "model and harness access" with two step kinds (model call,
harness session) sharing one budget and trace contract, and the `claude_call.py` rule in
`CLAUDE.md` generalizes to "every model or harness call goes through a budgeted step".

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

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
| M4 model access | passed | Catalog `modules/evaluation/models.yaml`; `model_call` sub-chain; `modules/systems/runs.py`; `ai/llm` not used (`docs/RULEGO_NOTES.md` §8) |
| M5 compile and run a component graph | passed | Typed port contracts; `modules/systems/graph.py`, `pclib/compile.py`, `pclib/component.py`; stubs in `tests/components/` |
| M6 sandbox | next | |

Test suite: 70 pass; skipped without their prerequisites: 8 smoke tests (`RULEGO_URL`, `RULEGO_DATABASE_URL`)
and one live model call (`ANTHROPIC_API_KEY`).

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

## Settled

- **What this is.** Three pieces that meet at a file-shaped seam and are each tested alone:
  the kernel (knows: sources, assertions, vocabularies), the component graph (describes:
  roles, typed ports, couplings, components, stored in the kernel), and the runtime (does:
  RuleGo + pclib run compiled graphs). Paper ingestion and agent design are one domain and
  one application on top.
- **Where code goes.** Modules carry meaning (vocabularies, constraints, views) and import
  only `kernel`. Components (steps that fill roles) and apps (chains for a purpose) import
  `kernel`, `pclib` and modules, and ship as RuleGo containers (chains + config; MCP comes
  with RuleGo-Server). Existing behaviour under `modules/` (`evaluation`'s `model_call`
  steps, `systems/runs.py`) moves out when next touched, not as a restructure.
- **pclib contracts.** Steps in a run use the run contract (checkpoint, replay, trace,
  spend); components add the port adapter. Stateless tools (e.g. MCP tools over the kernel)
  will get a thinner `tool` entry point when the first one is built.
- **Model access (M4).** Models, endpoints, parameters and prices are kernel facts; budget
  and usage live in the `model_call` sub-chain; `ai/llm` isn't used (RULEGO_NOTES §8). Other
  providers go behind an OpenAI-compatible router (e.g. LiteLLM) rather than new adapters.
- **One milestone at a time; ideas go to the backlog.** Including the knowledge system as an
  MCP server with a skill and harness hooks, harness sessions, `beliefs()`, a graph
  projection and entity resolution: all parked in `docs/BACKLOG.md`.

## Other known gaps

- `compose.yml` hasn't been run under Podman; the `rulego` container was tested with Docker.
- The Reflexion example was written from memory; M7 verifies it against the paper.
- Sandbox (M6) will need the rootless Podman socket and `podman-remote` in the RuleGo image.

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
- **RuleGo smoke tests:** create a `rulego_smoke` database with `db/*.sql`, then
  `docker run -d --network host -v $PWD:/repo:ro -v /var/lib/postgresql/kscratch:/pgsock
  -e DATABASE_URL="postgresql://postgres@/rulego_smoke?host=/pgsock&port=5499" -e FAKE_KEY=smoke
  localhost/kernel-rulego`, and run with `NO_PROXY=127.0.0.1,localhost`,
  `RULEGO_URL=http://127.0.0.1:9090` and `RULEGO_DATABASE_URL` pointing at `rulego_smoke`.
- **RuleGo container with the local database:** mount the socket directory,
  `-v /var/lib/postgresql/kscratch:/pgsock -e DATABASE_URL="postgresql://postgres@/<db>?host=/pgsock&port=5499"`.
- **Sources:** rulego.cc is blocked; the docs are in github.com/rulego/rulego-doc, the server
  in github.com/rulego/rulego (`server/`), components in rulego-components and
  rulego-components-ai. `git clone` and the Go module proxy work.

# Lessons from the first three iterations

Branches: [`first_iteration`](https://github.com/OpenReplicant/kernel/tree/first_iteration),
[`second_iteration`](https://github.com/OpenReplicant/kernel/tree/second_iteration),
[`third_iteration`](https://github.com/OpenReplicant/kernel/tree/third_iteration). Paths
below are on `third_iteration` unless they say otherwise.

## What went wrong

- **The first application was bigger than the core** (iteration two). The paper compiler
  needed a component compiler, benchmarks, a sandbox and model routing before the core loop
  ran.
- **Several things at once.** Ideas that mesh still have to be built one at a time.
- **Results outside the record** (iteration two). A calibration run passed 20/20, but nothing
  was recalled and the results never reached the graph. Every result that matters lands in the
  memory as evidence.
- **Memory and harness were one product** (iteration three). The epistemic core (claims,
  provenance, supersede, trace, the change log) is what matters, and it doesn't depend on a
  rule engine. RuleGo was both a harness (running models, tools, agents, a sandbox) and a
  framework for writing chains, and capabilities that belong to a harness (model access,
  sandbox, agent harnesses) kept landing in the "kernel". Split: this repo is the memory; a
  harness is a separate product that uses it over MCP.
- **Ingestion-heavy, retrieval-thin** (all three). Claims went in with care; getting the right
  ones back out was never built. Iteration three's "recaller" read named predicates of a known
  room (`modules/climate_recall`), and its vector search took vectors the caller supplied: no
  embedding model was ever wired in, no ranking, no token budget, no measure of whether recall
  was any good. Nothing kept the memory in order either (no entity resolution, conflicts or
  staleness beyond one hand-written reasoner). This time retrieval comes right after storage
  and is measured by a benchmark, and upkeep processors are first-class.

## What worked (carry over the ideas, port code deliberately)

| Idea | Where |
|---|---|
| AgensGraph 2.18.4 + pgvector 0.8.7, built from source in a cloud session | `db/agensgraph/` (Dockerfile, build.sh), `docs/SPIKE_NOTES.md` §1–2 |
| The core in SQL: claims with provenance enforced by triggers, times stamped by the database, no edits (supersede/retract), the outbox with causation and depth, the loop guard, refusing unlabelled elements and undeclared labels | `db/core.sql`, `docs/CORE.md` |
| Claims API: assert, supersede, retract, held, as_of, why, trace (`derived_from`) | `kernel/claims.py`, `tests/test_conventions.py`, `tests/test_gate.py` |
| Ontologies loaded from manifests: labels inherit `thing`, vector columns promoted with HNSW indexes | `kernel/modules.py` (`_install_ontology`) |
| Facts checked by experiment about AgensGraph (labels fold to lower case, Cypher creates unseen labels, SET drops nulls, jsonb parameters in property maps, triggers apply to Cypher writes) | `docs/SPIKE_NOTES.md` |
| Model calls recorded as sources: role → model → endpoint from claims, budgets, tokens, cost and the answering model | `kernel/models.py`, `kernel/providers.py`, `docs/MODELS.md` |
| `claude -p` as a model endpoint: `--tools "" --strict-mcp-config --setting-sources "" --no-session-persistence --system-prompt … --output-format json`, in an empty directory; sum tokens from `modelUsage` | `kernel/providers.py` |
| MCP Python SDK 2.3 servers and clients (stdio and streamable HTTP); `ToolError` for messages the client should see | `sandbox/server.py`, `kernel/nodes/sandbox_run.py` |
| A local fake model endpoint for tests (Anthropic and OpenAI shapes) | `tests/fake_model.py` |

## Cloud-session know-how (Claude Code on the web)

- **Containers:** no Podman. Start `dockerd` by hand (`nohup dockerd >/tmp/dockerd.log 2>&1 &`),
  again after every container restart. Docker Hub rate-limits: use `mirror.gcr.io/library/...`
  for official images; `skaiworldwide/agensgraph` pulled directly. Builds go through a
  TLS-intercepting proxy (`/root/.ccr/ca-bundle.crt`).
- **Network policy** is the environment's setting. Under the restricted policy of iterations
  two and three, huggingface.co, openrouter.ai and distro package mirrors were blocked, and
  pypi.org, the Go module proxy, `git clone` of public repos and raw.githubusercontent.com
  worked. The fourth iteration's environment has full internet access (not yet checked by
  experiment).
- **`claude -p`** works from the shell and from subprocesses and answers as Haiku in 3–4 s.
  **Claude Code passes its whole environment** (tokens included) to the stdio MCP servers it
  starts; start ours through `env -i` when Claude Code launches it.
- **`pkill -f <pattern>`** from a tool call can kill the calling shell when the command text
  contains the pattern; match by process name.
- **Secrets:** from the environment only; never in files. An OpenRouter key was pasted in chat
  during iteration two; rotate it.

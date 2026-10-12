# Lessons from the first two iterations

Paths below refer to the [`second_iteration`](https://github.com/OpenReplicant/kernel/tree/second_iteration) branch.

## What went wrong

- **The first application was bigger than the core.** The paper compiler needed a component
  compiler, benchmarks, a sandbox and model routing before the core loop (observe, believe,
  recall, act) ever ran. Build the loop first; applications later.
- **Several things at once.** Great ideas that mesh together still have to be built one at a
  time, each working alone and then together. Park the rest in the backlog.
- **The calibration run showed the gap.** Haiku passed 20/20 HumanEval problems; the kernel
  only configured the call and recorded metadata afterwards. Nothing was recalled, and the
  pass/fail results never reached the kernel because the check ran outside a traced step.
  Every result that matters has to land in the graph as evidence.

## What worked (carry over the ideas, port code when needed)

| Idea | Where it is |
|---|---|
| Claims with provenance, method, evidence spans, two times; supersede/retract, never edit | `kernel/`, `db/001_kernel.sql`, `docs/KERNEL.md` |
| Vocabularies (ontologies) loaded as data, constraints registered per module | `kernel/vocab.py`, `modules/*/vocab.yaml` |
| Sandbox for generated code: fresh container, no network, read-only root, limits, one read-only work dir, timeout removes the container | `pclib/sandbox.py`, `sandbox/Containerfile`, `tests/sandbox/` |
| Models, endpoints, parameters and prices as facts; a model call resolves from them and records tokens and the answering model | `modules/evaluation/models.py`, `models.yaml`, `providers.py` |
| `claude -p` as a model endpoint on the Claude Code subscription: `--tools "" --strict-mcp-config --setting-sources "" --no-session-persistence --system-prompt … --output-format json`, run in an empty directory | `modules/evaluation/providers.py` (`_claude_code`) |
| RuleGo facts verified by experiment: x/python node, `while` loops over sub-chains, switches, metadata, MCP serving | `docs/RULEGO_NOTES.md`, `rulego/` (server build with components) |
| Checkpoint/replay per step key and a run trace | `pclib/contract.py` (move into RuleGo aspects or wrappers this time) |
| Component graphs with typed ports compiled to chains | `pclib/compile.py`, `modules/systems/graph.py` |

## Cloud-session know-how (Claude Code on the web)

- **Containers:** no Podman. Start `dockerd` by hand (`nohup dockerd >/tmp/dockerd.log 2>&1 &`).
  Docker Hub rate-limits: use `mirror.gcr.io/library/...` for official images. Builds go
  through a TLS-intercepting proxy: base images must trust `/root/.ccr/ca-bundle.crt`; build
  with `--network host --build-arg HTTPS_PROXY=$HTTPS_PROXY`. Whether non-library images such
  as `skaiworldwide/agensgraph` pull through a mirror is unverified; building from source works
  if not (git clone of public github.com repos works).
- **Network policy:** openrouter.ai and huggingface.co are blocked; github.com is blocked for
  HTTP downloads of repos outside the session, but `git clone` and raw.githubusercontent.com
  work; pypi.org and the Go module proxy work.
- **Postgres:** binaries exist but no service; the second iteration's `tools/dev_postgres.sh`
  starts a throwaway socket-only cluster. Nothing survives a container restart: restart
  Postgres and dockerd after one.
- **`claude -p` from chains:** the RuleGo server must run where `claude` is logged in, i.e. on
  the host, not in its container. Copy the binary out of the image, symlink the repo to the
  path chains expect, point `data_dir` at a scratch dir, and start it with the repo's venv
  first on PATH.
- **Secrets:** from the environment only; never in files. An OpenRouter key was pasted in chat
  during iteration two; rotate it.

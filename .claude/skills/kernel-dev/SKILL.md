---
name: kernel-dev
description: Use when developing this repository (the Knowledge Kernel) — restoring the dev environment after a container restart, running the tests, evals and the Docker stack smoke test, adding a module or a RuleGo component, or shipping a task through CI and a pull request.
---

# Developing the Knowledge Kernel

Read `CLAUDE.md` first (rules, invariants), then `docs/milestone-1.md` (status of every task),
`docs/decisions.md`, and `docs/LESSONS.md` (what not to relearn).

## Restore the environment (cloud sessions restart without warning)

```sh
rm -f /var/run/docker.pid /var/run/docker/containerd/containerd.pid
(nohup dockerd > /tmp/dockerd.log 2>&1 &)
service postgresql start
python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -r requirements-ci.txt   # if gone
mkdir -p node_modules && ln -sfn /opt/node-tools/node_modules/playwright node_modules/playwright \
  && ln -sfn /opt/node-tools/node_modules/playwright-core node_modules/playwright-core
```

Go: `GOTOOLCHAIN=go1.25.0` (the local toolchain is older). Proxy CA: `/root/.ccr/ca-bundle.crt`.

## Check your work

```sh
su postgres -c "sh kernel/schema/tests/run.sh"            # 30 kernel checks (peer auth: run as postgres)
python shapes/tests/test_shapes.py && python scripts/check_manifests.py
python modules/<module>/tests/<test>.py                    # each module's tests and evals
PYTHONPATH=modules/pack-loader python -m pack_loader | diff -q - modules/pack-loader/packs.sql
(cd modules/chain-runner-rulego && gofmt -l . && go vet ./... && go test ./...)
PYTHON=/tmp/venv/bin/python KK_BUILD_CA=/root/.ccr/ca-bundle.crt sh scripts/smoke_stack.sh   # gates G1–G5, ~5 min
```

Regenerate `modules/pack-loader/packs.sql` after changing `ontology/*.ttl`, `slots.yaml` or any
chain file (it registers chain and step IRIs). Scan for secrets before pushing:
`docker run --rm -v "$PWD":/repo:ro zricethezav/gitleaks:v8.30.1 dir /repo --redact --exit-code 0`.

## Ship a task

One task per PR: tests and the gate pass locally, a decision entry in `docs/decisions.md` if
anything was decided (append-only: supersede, don't edit), the status column in
`docs/milestone-1.md`, push, wait for CI (all four jobs), then open and merge the PR and reset the
working branch onto `main`. When CI fails, read the failed job's log before changing anything; the
smoke test prints module logs on failure.

## Adding things

- **A module:** `modules/<name>/` with `module.yaml` (schema `kernel/schemas/module.schema.json`,
  slots from `ontology/slots.yaml`), `migration.sql` (role + agent row, see `modules/_template`),
  code that reads through PostgREST with its own token and never writes tables, tests, and a
  Dockerfile whose build context is the repo root. Services return change sets; chains submit them.
- **A chain:** see `modules/chain-decoder-rulego/SKILL.md`.
- **A RuleGo component:** see `modules/chain-runner-rulego/README.md`.
- **A vocabulary term:** check `ontology/packs.yaml` for the owning vocabulary first; kk terms go in
  `kk-core.ttl` with a version bump.

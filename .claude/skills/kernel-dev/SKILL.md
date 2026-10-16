---
name: kernel-dev
description: Use when developing this repository (the Knowledge Kernel) — restoring the dev environment after a container restart, running the tests, evals and the kernel smoke test, adding a module, or shipping a task through CI and a pull request.
---

# Developing the Knowledge Kernel

Read `CLAUDE.md` first (rules, invariants), then `docs/milestone-1.md` (status of every task),
`docs/decisions.md`, and `docs/LESSONS.md` (what not to relearn).

## Restore the environment (cloud sessions restart without warning)

```sh
rm -f /var/run/docker.pid /var/run/docker/containerd/containerd.pid
(nohup dockerd > /tmp/dockerd.log 2>&1 &)
service postgresql start
python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -r requirements-ci.txt \
  -r modules/kernel-mcp/requirements.txt -r modules/validator-shacl/requirements.txt \
  -r modules/belief-corroboration/requirements.txt   # if gone
```

Proxy CA: `/root/.ccr/ca-bundle.crt`.

## Check your work

```sh
su postgres -c "createuser -s root" 2>/dev/null           # once: lets root use psql over the socket
su postgres -c "sh kernel/schema/tests/run.sh"            # 30 kernel checks (peer auth: run as postgres)
python shapes/tests/test_shapes.py && python scripts/check_manifests.py
python modules/<module>/tests/<test>.py                    # each module's tests and evals
PYTHONPATH=modules/pack-loader python -m pack_loader | diff -q - modules/pack-loader/packs.sql
PGDATABASE=postgres PYTHON=/tmp/venv/bin/python sh scripts/smoke_kernel.sh   # G4: kernel alone, ~10 s
```

The venv also needs `modules/kernel-mcp/requirements.txt` and `modules/validator-shacl/requirements.txt`.
Run the smoke test as root, not as postgres: the postgres user cannot read the proxy CA that
`SSL_CERT_FILE` points to. Regenerate `modules/pack-loader/packs.sql` after changing `ontology/*.ttl`,
`slots.yaml` or any chain file. Docker image builds behind the proxy:
`docker build --secret id=ca,src=/root/.ccr/ca-bundle.crt -f modules/<m>/Dockerfile .`. Scan for secrets
before pushing: `docker run --rm -v "$PWD":/repo:ro zricethezav/gitleaks:v8.30.1 dir /repo --redact --exit-code 0`.

## Ship a task

One task per PR: tests and the gate pass locally, a decision entry in `docs/decisions.md` if
anything was decided (append-only: supersede, don't edit), the status column in
`docs/milestone-1.md`, push, wait for CI (all jobs), then open and merge the PR and reset the
working branch onto `main`. When CI fails, read the failed job's log before changing anything; the
smoke test prints module logs on failure.

## Adding things

- **A module:** `modules/<name>/` with `module.yaml` (schema `kernel/schemas/module.schema.json`,
  slots from `ontology/slots.yaml`), `migration.sql` (role + agent row, see `modules/_template`),
  code that reads the `api` schema under its own role (`DATABASE_URL` + `SET ROLE`, as the validator
  does) and never writes tables, tests, and a Dockerfile whose build context is the repo root. It must
  run without a chain runner (D-36).
- **Parked work** (`parked/`): see `parked/README.md` before reviving anything.
- **A vocabulary term:** check `ontology/packs.yaml` for a standard vocabulary that owns the concept
  first (T12 moves us off `kk:` terms); only the module contract stays in `kk-core.ttl`.

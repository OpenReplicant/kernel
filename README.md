# kernel

An evidence-based world model, and a factory that builds runnable systems on top of it.

- **The kernel** stores what is believed about things, how they are structured and
  connected, and where every belief came from: a document, a direct observation, a report, a
  session transcript. Facts are never edited; every one traces back to the recording that
  supports it.
- **Systems** described in the kernel compile to [RuleGo](https://github.com/rulego/rulego)
  chains of Python steps and run; what a run observes goes back into the kernel as evidence.
- **Modules** teach it domains. The first application, the **paper compiler**, turns an
  agent-design paper into a decomposed, runnable system, checks that the paper's mechanism is
  actually present, and records the results.

The same substrate is meant to serve as a research assistant, an agent factory and a
software factory (from a spec or an interactive session), either on its own or alongside an
agent harness. Every piece is a chain or a step, so custom chains can replace any part of it.

## Layers

| Layer | What | Where |
|---|---|---|
| L0 kernel | Evidence store, upper ontology, vocabulary/constraint/view/module machinery | `kernel/`, `vocab/kernel.yaml`, `db/` |
| L1 systems | Role instances, components, ports and couplings, boundary, behaviour tests | `modules/systems/`, `pclib/`, `rulego/` |
| L2 domains | Agent design (slot types and their rules), evaluation (models, benchmarks, results) | `modules/agent_design/`, `modules/evaluation/` |
| L3 applications | Paper compiler: papers as evidence, the `spec.yaml` view, reports | `modules/paper_compiler/` |

Details: `docs/ARCHITECTURE.md`.

## Status

Version 1 follows `docs/V1_BUILD_PLAN.md`. Gates passed: **M0** (RuleGo spike), **M1** (script
contract), **M2** (kernel core), **M3** (vocabularies, validation, views, modules). Next: **M4**,
model access, whose shape is being revisited (`docs/NOTES.md`).

## Running

```sh
python -m venv .venv && .venv/bin/pip install -e ".[test]"
podman compose up -d postgres                 # or any Postgres 16; see .env.example
export DATABASE_URL=postgresql://kernel:kernel@127.0.0.1:5432/kernel
.venv/bin/pytest                              # each session creates and drops its own database

# RuleGo-Server with this repo's steps (rulego/README.md)
podman compose up -d rulego
RULEGO_URL=http://127.0.0.1:9090 RULEGO_DATABASE_URL=$DATABASE_URL .venv/bin/pytest tests/smoke
```

## Map

| Path | What |
|---|---|
| `kernel/` | The kernel library (`Kernel.connect()`), domain-free |
| `vocab/kernel.yaml` | The upper ontology: knowing (agents, sources, reliability) and structure (parts, roles, ports, couplings) |
| `modules/<name>/` | `module.yaml`, `vocab.yaml`, `register()`; see the layers above |
| `pclib/` | The step contract, the RuleGo node (`node.py`), chain builder (`chains.py`), RuleGo client (`rulego.py`) |
| `rulego/` | The runtime build: our RuleGo-Server module, Containerfile, config, M0 spike |
| `scripts/` | Tool-shaped scripts (JSON in and out, schemas in `scripts/schemas/`) |
| `db/` | Kernel and runtime schemas; the database enforces the evidence rules |
| `examples/reflexion/` | A worked spec; `schema/` its JSON Schema; `tools/validate_spec.py` its validator |
| `tests/` | `kernel/`, `contract/`, `modules/`, `smoke/` |

## Docs

`docs/VISION.md` (why) · `docs/ARCHITECTURE.md` (layers, modules) · `docs/KERNEL.md` (the
contract) · `docs/CONCEPTS.md` (agent-design vocabulary) · `docs/V1_BUILD_PLAN.md` (milestones)
· `docs/SCRIPT_CONTRACT.md` (steps) · `docs/RULEGO_NOTES.md` (runtime findings) ·
`docs/FILESYSTEM.md` (where things live) · `docs/NOTES.md` (current state, open decisions) ·
`CLAUDE.md` (rules for Claude Code sessions)

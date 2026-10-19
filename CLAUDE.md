# Knowledge Kernel

A small knowledge kernel that drops into any agent harness. One Postgres holds every claim
(attributed, referenced, belief-scored, scoped); its three jobs are ingestion, processing and recall,
each done by a swappable default module; everything else is a module too. Integration is a ladder:
MCP only → + skill file → + harness hooks → + further modules (D-36). Almost every component is
existing open tooling — what this repo builds is the kernel, its conventions and the default modules.

Positioning (D-84): for developers, an agent memory layer with provenance and clearance; for
organisations, a knowledge management system sold as **distributions** (preset bundles of modules, packs,
skills and views that do one job; the first is org and process maps from conversations).

## Read first, in this order

1. `docs/spec.md` — the specification (source of truth). Kernel v0 (§3) is **frozen**.
2. `docs/milestone-6.md` — current work (agent memory without baggage); milestones 1 (the kernel), 2 (BPM and devops), 3 (your own data, cited) and 4 (a demo you can sell) are done, and `docs/milestone-5.md` has the interviewer and what carries over.
3. `docs/decisions.md` — decided, adopted (confirm with the owner) and open questions.
4. `kernel/schema/001_kernel.sql` and its tests — what the kernel actually enforces.
5. `docs/LESSONS.md` — what earlier sessions learned the hard way. The `kernel-dev` skill
   (`.claude/skills/kernel-dev/SKILL.md`) restores the environment and runs every check.

If this file and the spec disagree, the spec wins; flag the conflict instead of picking silently.

## Invariants (never weaken; changing them needs the owner's explicit decision)

1. Every claim is attributed (agent + run) and referenced; extracted claims carry the supporting quote.
2. Nothing is deleted: supersede, refute or retract by status. The only content removal is redaction,
   which keeps identity and lineage and is itself a recorded run.
3. A claim's scope is at least as restrictive as everything it was made from.
4. Clearance is granted by people and enforced by Postgres, never by a model's judgment.
5. No method verifies its own output.
6. Every answer cites claim ids and shows their belief.

## Rules for working in this repo

- **Kernel changes are off-limits** without the owner's decision. If a feature seems to need one, build
  it as a module that reads `api` views and submits change sets, or write up the case in
  `docs/decisions.md` and ask.
- **Modules never write tables.** They read the `api` schema under their own database role (directly,
  with `kk.lib`) and write only by `submit_change_set`. Actuators never write claims.
- **Keep the kernel small (D-36).** No required middleware or runner: anything that needs one is a
  module. Prefer compatibility with existing tools over building our own. Work outside the kernel is
  parked under `parked/` until revived.
- **One concept, one home** (`ontology/packs.yaml`). Before adding a term, check whether a standard
  vocabulary owns the concept; `kk:` is only for the module contract (D-40). New terms go through the review lane (vocabulary typing claims are
  forced there) or a pack migration.
- **Look things up; don't rely on memory** for external tools (MCP SDK v2, psycopg, pySHACL,
  gitleaks, image tags). Pin exact versions and record what you pinned.
- **Every module** has a `module.yaml` valid against `kernel/schemas/module.schema.json`, fills slots
  from `ontology/slots.yaml` and runs as its own `kkm_*` role.
- Conditions go in qualifiers, never inside values. Ambiguous entity matches stay separate with
  `skos:closeMatch` for review. Telemetry is aggregated into observed claims per time window.
- Licenses: permissive or LGPL-library only in the default distribution (D-11).

## Working with the owner

- Recommend one option and keep moving. The owner has decision fatigue; ask only for decisions that are
  theirs (kernel changes, positioning, licences) and say what you recommend.
- When asked to be dialectical, give the strongest objections, then a synthesis that keeps what survives.
- Plain words. Don't write "...and it's worth...".
- Evals that need a model use OpenRouter `:free` models under 10B parameters (Oct 2026: `liquid/lfm-2.5-2.6b:free`)
  and/or Haiku as harness sub-agents (answers files, as SciFact's `--answers`); never paid models.
- Each task ships the same way: implement, test (unit tests, `smoke_kernel.sh`, and `e2e_module.sh` and
  `e2e_devops.sh` when relevant), gitleaks, commit, push, PR, CI green, squash merge, reset the branch to
  main (the `kernel-dev` skill).

## Tests

```sh
PGHOST=<socket or host> PGUSER=<superuser> sh kernel/schema/tests/run.sh   # 30 kernel checks
pip install -r requirements-ci.txt
python shapes/tests/test_shapes.py                                         # SHACL fixtures
python scripts/check_manifests.py                                          # manifests and slots
python scripts/catalog.py --check                                          # what modules take in and give out (docs/modules.md)
python modules/<module>/tests/*.py                                         # each module's tests and evals
pip install -e .                                                           # the kk command and the default modules
sh scripts/smoke_kernel.sh                                                 # G3, G4, G7, T13, T17, T19, T20, M2, D-60 (recall), D-61 and D-66 (dense, re-ranking; with pgvector and fastembed), D-62 (belief from evidence), M3-T6 (quote verifier), M3-T1 (kk up), M3-G1 (clone to a cited answer), M3-G2 (cited answers checked, scores as claims), M4-T1 (sign-in), M4-T3 (the process learns, in the UI), M4-G1 (kk demo, end to end), M5-T3 (interaction encoder), M5-T4 (the interviewer) on kk alone; needs gitleaks
                                                                           # and, for the UI, npm install --no-save playwright@1.56.1 bpmn-moddle@10.3.1
sh scripts/e2e_module.sh                                                   # T18, M2-G3, M3-G3: modules in their own sandboxed containers; needs Docker
sh scripts/e2e_devops.sh                                                   # M2-T5–T7, M2-G2: devops demo, MAPE-K loop, Devops view; needs Docker, gitleaks
```

CI (`.github/workflows/ci.yml`) runs all of these. The kernel smoke test needs no Docker: a Postgres
superuser (PGHOST/PGUSER/PGPASSWORD) and python3 with this repository installed (`pip install -e .`). Docker builds behind a TLS-intercepting proxy take the proxy's CA as a build
secret (`--secret id=ca,src=/path/ca.crt`).

## Layout

```
kernel/
  schema/        001_kernel.sql, 002_bootstrap.sql, tests/      ← frozen v0
  schemas/       change_set and module JSON Schemas             ← ABI
  contracts.py   Python Protocols for module kinds              ← ABI
ontology/        kk-core.ttl (module contract only), dcterms.ttl, prov.ttl, activitystreams.ttl, org.ttl, profiles kk-tosca/kk-profile/bbo/sosa/fabio/cito,
                 observables.ttl, conformance.ttl, oa.ttl, mappings.ttl, codemeta.ttl (packages), dqv.ttl (eval scores),
                 packs.yaml (packs + catalogs), slots.yaml
shapes/          kernel.shacl.ttl + fixtures/tests
modules/         default modules: kernel-mcp, validator-shacl, belief-corroboration, self-encoder,
                 ingest-filter-gitleaks, pack-loader, ui (shell at /ui), view-modules (a view); milestone 2:
                 encoder-eventlog, encoder-bpmn, skill-process-extraction, conformance,
                 view-bpm, observer-prometheus, anomaly, skill-adaptation-planning, actuator-compose,
                 view-devops; milestone 3: encoder-papers, recall-index (BM25, default), recall-dense (embeddings + BM25, opt-in),
                 verifier-quotes (quotes occur in their sources, or in the claims they cite), evaluator (eval scores as DQV claims);
                 milestone 4: learner-process (the Learn phase for processes: approved revisions of the documented process);
                 milestone 5: encoder-interaction (chats and agent sessions as ActivityStreams messages and PROV activities),
                 skill-interview (an agent interviews a person: org and process maps, quoting each message),
                 map-gaps (what the maps lack, as the questions to ask next);
                 milestone 6: truth-maintenance (flags claims whose premises were withdrawn; `docs/reasoning.md`),
                 memory-extractor (memories from recorded messages, quoting them, with a small model);
                 _template/
kk/              the kk command (up, demo, init, person/agent add, serve, status, review, scan, token, module,
                 export/import) + kk.lib (modules) + kk.changes (supersede on re-ingest)
deploy/          compose.yaml (db, init, kernel = kk serve, caddy), policy.yaml (sandbox grants), demo/ (devops demo overlay), postgres/ (image),
                 modules/ (installed), mcp/
scripts/         smoke_kernel.sh + e2e_kernel/ingest/transfer/bpm.py + e2e_ui.mjs, e2e_module.sh/.py/_m3.py,
                 e2e_devops.sh/.py + e2e_devops_ui.mjs, eval_self.py,
                 check_manifests.py, vocab_subset.py
examples/        demo data (order-to-cash: event log and its generator, BPMN model, written procedure; bpi2019: the documented model)
parked/          modules and scripts outside the kernel (runners, decoders, UI), not in CI (D-36)
eval/            eval sets (data): validator, belief-v0, self-model (questions.yaml, runs/), scifact, rcaeval, bpi2019 and longmemeval (run.py; data fetched, not committed)
docs/            spec, architecture (plain language), developer-guide (the moving parts and interfaces), milestone-1..5, decisions, modules (generated catalog), demo, interaction-research, harness (drop-in guide), mcp-servers, sources, recall-research, benchmarks; spikes/
```

## Current milestone

Milestone 6 (`docs/milestone-6.md`, D-85): agent memory without baggage. Rigour behind the interface:
passive capture, and an MCP `memory` profile of four tools. Briefings are cited and attributed within a
token budget, with what changed since the agent last looked. Each agent has a working-memory scope.
Terms are chosen inside the extractor, never by the agent, and a compaction verifier checks summaries.
Reactivity is D-86: `current_edge` and a cursor in the kernel, detectors as a module. M6-T1 is done (D-88): `kk up` gives an agent the four-tool memory profile. Reasoning over claims
(`docs/reasoning.md`, D-89): conclusions with premises and truth maintenance are built (D-90). The owner
delegated decisions (D-87): D-86 is confirmed, O-8 is closed, and adoption comes first (`docs/offer.md`). Work one task at a time; each ends with its check passing.

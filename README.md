# Knowledge Kernel — handoff

Everything needed to start building in a fresh Claude Code session. This replaces the earlier
`governed-wiki-handoff.zip` and the Wikibase-based "Governed Wiki Knowledge Core" design.

**Start here:** `KICKOFF.md` (what to paste into Claude Code), then `CLAUDE.md` (the project brief).

## What's in the box

| Path | What | State |
| --- | --- | --- |
| `docs/spec.md` | The specification: kernel, ABI, ontology tiers, system-modeling profile, chains, agents, MAPE-K, milestones | final for milestone 1 |
| `docs/milestone-1.md` | The self-model milestone: 14 tasks, 7 gates | ready |
| `docs/decisions.md` | 20 decisions (some "adopted — confirm") and 7 open questions | ready |
| `docs/mcp-servers.md` | Default, optional and excluded MCP servers, with licenses, flags and CVEs | researched Oct 2026 |
| `docs/sources.md` | Research lineage | — |
| `kernel/schema/` | Kernel v0: SQL schema + bootstrap + invariant tests | **tested**: 30 checks pass on Postgres 16 |
| `kernel/schemas/` | JSON Schemas: change set, module manifest, chain | validated against every manifest and chain |
| `kernel/contracts.py` | Python Protocols for module kinds | imports; output validates against the schema |
| `ontology/` | `kk-core.ttl`, `kk-tosca.ttl`, `kk-profile.ttl`, `packs.yaml` (tiers), `slots.yaml` (42 slots) | parse cleanly |
| `shapes/` | SHACL shapes + valid/invalid fixtures | **tested** with pySHACL |
| `modules/` | 12 module manifests (+ template) with chains for validator, belief, self-encoder, ingest filter; kernel MCP skill; starter UI page | manifests valid; implementations are milestone-1 work |
| `deploy/` | Compose stack (Postgres, PostgREST, Caddy; module, RuleGo and realtime profiles), Postgres image, init script, RuleGo rules, agent MCP config | `docker compose config` passes; images to pin |
| `scripts/` | `mint_jwt.py` (module/agent JWTs for PostgREST), `check_manifests.py` (manifests, chains, slots) | tested |
| `eval/` | Eval-set format and the milestone-1 sets | format only |

## Run the checks

```sh
PGHOST=... PGUSER=postgres sh kernel/schema/tests/run.sh
pip install rdflib pyshacl pyyaml jsonschema
python shapes/tests/test_shapes.py
python scripts/check_manifests.py
```

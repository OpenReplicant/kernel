# Writing a pack

A pack specialises the kernel for a domain: it adds kinds, edge specialisations and rules,
teaches agents the domain with a skill, and may ship servers that fetch outside data. The
kernel enforces its rules; it never adds node types or kernel edges
([ADR 0015](decisions/0015-packs-declare-their-ontology.md)). `packs/research/` is the
worked example with a server; `packs/software/` and `packs/process/` the ones with adapters;
`packs/bpm-reference/` is the kernel's own test pack.

## Layout

```
packs/<name>/
  SKILL.md            the agent's guidance; frontmatter names the pack and its versions
  schema.yaml         namespaces, kinds and edge kinds
  rules.yaml          rules the kernel checks inside every write
  mcp/                optional: servers, each a uv workspace member with its own pyproject.toml
  adapter/            optional: an adapter that writes through the gateway, also a workspace member
  evals/fixtures/     scripted fixtures with expected graphs (make eval)
  evals/live/         live-harness scenarios (make live SCENARIO=<name>)
  tests/              pytest; conftest.py names the packs its databases get
```

## SKILL.md

An Agent Skill: the folder is copied into the folders harnesses scan, so the root
`SKILL.md` must stand alone. Its frontmatter adds two fields the installer reads.

```yaml
---
name: research                 # lower case letters, digits and hyphens; the pack's name
description: >
  When an agent should use this pack, in one paragraph.
metadata:
  version: "0.1.0"             # the pack's version (semantic versioning)
  kernel: ">=0.2 <1.0"         # the kernel versions it supports
  namespace: research
  requires: world-model-core
---
```

## schema.yaml

Every entry needs a `label` and a `description`: `get_schema_slice` matches passages
against them.

```yaml
namespaces:
  - {name: research, label: Research, description: Scholarly research: papers, methods, findings.}
kinds:                          # node_type: Entity, Agent or Event (Claim kinds are modalities)
  - {name: paper, node_type: Entity, label: Paper, description: A scholarly publication.}
edge_kinds:                     # each specialises one kernel edge
  - {name: authored, edge: responsible_for, label: authored, description: A person is an author of a paper.}
```

Kind and edge-kind names are global across packs: pick names that will not collide.
Core kinds (`concept`, `data`, `component`, `role`, …) can be allowed in your namespace
by a `types` rule instead of being redefined.

## rules.yaml

Rule ids start with one of the pack's namespaces. Categories and their `params`:

| Category | params |
| --- | --- |
| `types` | `{kinds: [...]}`: the kinds allowed in the rule's `namespace` |
| `domain_range` | `{edge, kind?, from: {types?, kinds?}, to: {types?, kinds?}}`: allowed endpoints |
| `cardinality` | `{edge, key: from or to, key_kinds: [...], max}`: single-valued edges |
| `time` | `{check: window_order or event_order}`, or `{edge, require_valid_from: true}` |
| `identity` | `{node_type, kinds: [...], keys: [...], patterns?: {key: regex}}`: identity keys |
| `provenance` | `{edge?, modality?, min_basis?, requires_source?, basis?}` |

```yaml
rules:
  - id: research.authored_paper
    category: domain_range
    label: People author papers
    description: authored goes from a person to a paper.
    params: {edge: responsible_for, kind: authored, from: {types: [Agent], kinds: [human]}, to: {kinds: [paper]}}
```

## Installing

```sh
uv run python -m kernel.packs check                  # validate every pack folder
uv run python -m kernel.packs install research       # into $WMK_DATABASE via $WMK_ADMIN_DSN
WMK_PACKS="research" make up                          # the stack's packs service installs these
```

`install` accepts pack names (folders under `packs/`) or folder paths. The kernel refuses
a pack whose range excludes its version, terms that another pack or the core defines,
references to unknown edges or kinds, and upgrades that drop or retype a term. Upgrades
may add terms and change labels, descriptions and rule params; bump `metadata.version`.
Installing the same version again is a no-op.

## Tests

`kernel.testing` is a pytest plugin (enabled in the root `conftest.py`) with throwaway
kernel databases and helpers: `kdb` (a `KernelDB` for SQL-level writes and queries),
`dbname`, `agent`, `gateway_client(dbname)` (an in-process MCP client on the gateway),
`Rejected`. Name your pack's packs in `tests/conftest.py`:

```python
import pytest


@pytest.fixture(scope="session")
def wmk_packs() -> tuple[str, ...]:
    return ("research",)
```

Give test files names unique across the repository (`test_research_*.py`).

## Evals

A fixture folder holds `fixture.yaml` (sources, `packs: [...]`, thresholds), `script.yaml`
(the tool calls an extractor makes), `expected.yaml` (the graph it should produce) and
`sources/`. `make eval` finds fixtures in `evals/fixtures` and every
`packs/*/evals/fixtures`, gives each a database with only its packs, and scores entities
and edges. A live scenario in `evals/live/` runs a real model (`make live SCENARIO=<name>`);
its document `file` paths are relative to the scenario.

## Servers

A server that wraps an outside service lives in `mcp/` as a uv workspace member with its
own `pyproject.toml`, `Dockerfile` and dependencies, runs beside the gateway, and never
gets database credentials ([ADR 0014](decisions/0014-pack-servers-run-beside-the-gateway.md)).
Libraries follow [docs/adapters.md](adapters.md) and [ADR 0012](decisions/0012-dependency-licences.md).

## Adapters

An adapter maps structured data deterministically, without a model: it reads files or
APIs, builds a plan of sources and claims, and plays it through the gateway's tools as an
MCP client, so every write goes through `kernel.write` and the adapter needs no database
credentials. Give each file a `collection` that stays the same across its versions, so a
newer version supersedes the older one, and map each changed file in an extraction run:
closing the run retracts what the previous run found and this one did not
([ADR 0020](decisions/0020-quotes-and-extraction-runs.md)).
Render the plan as an eval fixture script and score it against a hand-written expected
graph. The plan, the code that plays it through the gateway and the script renderer are
shared: `adapter-kit/` (`wmk_adapter.plan`, `.apply`, `.script`; a workspace member your
adapter depends on). A plan can also assert on, or deny, an edge that already exists by its
`edge_id` (`Plan.on_edge`), which is how a check against the mapped model records its
verdicts. `packs/software/adapter/` (`wmk-software`) and `packs/process/adapter/`
(`wmk-process`, which reads event logs and checks the mapped process against them) are the
examples ([ADR 0017](decisions/0017-software-pack-and-the-self-boundary.md),
[ADR 0027](decisions/0027-process-pack-and-event-logs.md)).

## Moving a pack out of this repository

A pack folder depends only on the manifest format, the gateway's tools, `kernel.testing`,
the eval runners and, for an adapter, the adapter kit. To move one: copy the folder, depend on the kernel package for tests and
evals, point `WMK_PACKS` at its path, and run its tests against a kernel database. Not
built yet: a registry, fetching packs by URL, and pack SQL in a pack's own schema.

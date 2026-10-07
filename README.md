# World Model Kernel

A lightweight world model any agent can plug into. Agents write **claims** plus the
**graph operations** they justify; the kernel validates them against ontology rules and
commits the claim, the log entry and the graph change in one Postgres transaction. The
log is append-only and is the source of truth; the graph is its projection, and replaying
the log rebuilds it exactly.

Every fact keeps who said it and where, when it was true in the world and when the
kernel learned it, and whether credible sources agree (accepted) or disagree (contested).

**What it enables:** [`docs/use-cases.html`](docs/use-cases.html) lists 62 use cases in
business processes, software and IT, compliance, research, AI agents, teams, data
integration and specific sectors. Each is marked as working today, on the roadmap, a small
step from what exists, or needing a new pack. The page also suggests products to build on
the kernel, places for developers to contribute, and where the kernel does not fit. It is
an HTML page with a filter and search: open it in a browser from a clone, since GitHub
shows its source.

Design: [`docs/design-v1.md`](docs/design-v1.md). Decisions:
[`docs/decisions/`](docs/decisions/); where it goes next is
[ADR 0026](docs/decisions/0026-phase-3-the-closed-loop.md), in the order of
[ADR 0035](docs/decisions/0035-what-a-client-pays-for-comes-first.md). Libraries to adopt
for adapters: [`docs/adapters.md`](docs/adapters.md).

**Next (Phase 3):** what a client pays for comes first
([ADR 0035](docs/decisions/0035-what-a-client-pays-for-comes-first.md)). Each deliverable is
a document whose every finding cites its sources:
- the discovery report, from a client's folder of documents, transcripts and exports;
- a controls test: the client's written rules tested against every case in an export, with
  each exception listed;
- change-approval evidence for an audit period.

The rest of the loop follows when a paying pilot needs it: one part of the work automated
on a process runtime, what runs checked against the map, and the system proposing changes
to what it deployed, which a person approves.

## Quick start

```sh
make up        # Postgres 18 (AGE, pgvector, pg_trgm) + the MCP gateway on :8000
make seed      # optional: write the eval fixtures through the gateway
make northwind # optional: Northwind's purchase requests as written, told and done, in a discovery report
make replay    # rebuild the graph from the log and diff it against the live graph
make up-ui     # optional: the explorer on http://localhost:8080
make map-self  # optional: map this repository and the kernel's own boundary (software pack)
```

Connect any MCP harness to `http://localhost:8000/mcp` (streamable HTTP; published on
localhost only, since the stack has no authentication) and give it the core skill in
[`skills/core/`](skills/core/SKILL.md), plus [`skills/interview/`](skills/interview/SKILL.md)
when a person is the source. For a stdio harness, run the gateway with
`WMK_TRANSPORT=stdio uv run wmk-gateway`. `make up-otel` adds an OpenTelemetry
Collector and Jaeger (http://localhost:16686).

For research, `make up-research` adds the research pack's paper-source server on
`http://localhost:8001/mcp` (arXiv, OpenAlex, Crossref): connect it beside the gateway and
give the harness [`packs/research/`](packs/research/SKILL.md) as a skill. Set
`WMK_PAPERS_MAILTO` to a contact email for the APIs' polite pools, and
`WMK_PAPERS_OPENALEX_API_KEY` (free) to search OpenAlex.

The explorer (`make up-ui`) shows what agents wrote: the log, nodes and edges with their
belief and assertions, each source with the claims drawn from its passages, the evidence
between claims, a graph view, an index of models (each area of the graph, and each system
with its parts) and the installed ontology. It reads through the reader role
([ADR 0016](docs/decisions/0016-a-read-only-explorer.md)).

Its Proposals page lists what agents propose, with what each changes, its evidence and the
approvals it needs. A person signs in there to approve or reject one, or to protect a node
as an instrument; nothing else in the explorer writes
([ADR 0030](docs/decisions/0030-signing-in-to-decide.md)). To turn sign-in on, start the
explorer with a secret of at least 32 characters, then mint a token for each person:

```sh
export WMK_JWT_SECRET=$(openssl rand -hex 32)
make up-ui
make token EMAIL=dana@example.org NAME="Dana Ruiz"   # paste it into "Sign in to decide"
```

The secret stays with the operator: anyone holding it can mint a token for anyone. Without
it, the explorer only reads.

For software and operations, the software pack's adapter maps a repository's compose file,
Dockerfiles, `pyproject.toml`, CI workflows and git history through the gateway:
`uv run wmk-software map <repo> --url http://localhost:8000/mcp`. Mapping again writes only
what changed and retracts what a file no longer says. `make map-self` maps this repository
with the self boundary: the system this kernel instance is, and its parts, each a claim
with its own belief ([ADR 0017](docs/decisions/0017-software-pack-and-the-self-boundary.md)).
`make observe-self` then captures what actually runs from Docker and checks it against
what was mapped. An image tag, a port or a service the repository declares and the host
contradicts becomes contested: drift. Unhealthy containers open incidents that close when
the service recovers ([ADR 0028](docs/decisions/0028-what-runs-observed.md)).

The forge is where code is reviewed and merged. `make forge-self` reads this repository's
pull requests from GitHub (read-only), writes each one's author, approvals and merge as
evidence, and audits every change on `main`. Each change is one of:
- decided by an approved proposal;
- reviewed on the forge by someone other than its author;
- approved only at an earlier commit;
- unreviewed;
- merged through no pull request the kernel knows.

The audit also lists what runs from changes nobody approved, using the commit each image is
labelled with. A forge's approval is evidence of a decision, never the decision
([ADR 0031](docs/decisions/0031-the-forge-as-evidence.md)).

For business processes, the process pack maps how work happens as told (interviews), as
written (SOPs) and as done (event logs), with steps joined by flows whose conditions any
workflow runtime can compile. Its adapter reads CSV, XES and OCEL 2.0 logs:
`uv run wmk-process discover <config> --url ...` writes the log's view, and
`uv run wmk-process conform <config> --url ...` checks the mapped process against the
log. A step or branch the SOP states and the log denies becomes contested
([ADR 0027](docs/decisions/0027-process-pack-and-event-logs.md)).

`make northwind` takes Northwind's purchase requests through all three views. Its SOP and
two interviews are a scripted fixture standing in for the extractor, with each answer
sealed under its speaker's key. The log maps onto the same nodes and checks them, and
`uv run wmk-process compare <config> --url ...` prints where the views disagree
([ADR 0032](docs/decisions/0032-northwind-as-told-and-as-written.md)):

```text
Contested (some source asserts, some denies):
- Check budget [contested]: as written asserts; as told denies; as done denies.
- Over 10,000 euros? -> Approve purchase request, when amount > 10000 [contested]: as written asserts;
  as told divided (interview:nw-2026-09-15-a denies, interview:nw-2026-09-16-b asserts from 2026-08-01);
  as done denies.
```

`uv run wmk-process rank <config> --url ...` then scores each step on what the kernel holds:
volume, time since the previous event, rework, handoffs between roles, routing rules some
source denies, and whether a system runs it. Every input sits next to its factor, and the
contested facts to settle first are listed with it
([ADR 0033](docs/decisions/0033-ranking-what-to-automate.md)):

```text
2. Approve purchase request: 3.84
   volume    0.26  36 executions
   waiting   0.57  1958.2 hours in total since the case's previous event, median 52.2
   rework    0.00  0 repeats
   handoffs  1.00  36 after another role's step
   rule      1.00  Over 10,000 euros? -> Approve purchase request, when amount > 10000 [contested]
   system    1.00  Coupa
   settle first: Over 10,000 euros? -> Approve purchase request, when amount > 10000 [contested]
```

`uv run wmk-process report <config> --url ...` puts it all in one Markdown document, the
discovery report: the process as mapped, where the views disagree, the measures and the
ranking. Each sentence ends with footnotes citing the claims it rests on, with the words of
their sources. `make northwind` prints it
([ADR 0034](docs/decisions/0034-the-discovery-report.md)):

```markdown
- Over 10,000 euros? -> Approve purchase request, when amount > 10000 [contested]
  - As written, sop:fin-007, states it. [^17]
  - As told, interview:nw-2026-09-15-a, denies it. [^37]
  - As told, interview:nw-2026-09-16-b, states it from 2026-08-01. [^38]
  - As done, conformance:purchase-requests, denies it. [^39]

[^38]: as told, interview:nw-2026-09-16-b, reported at log offset 120: “Since I started in August, every request over 10,000 euros comes to me after the line manager's review”
```

## The tools

| Tool | Tier | Does |
| --- | --- | --- |
| `write` | Write | Submits a claim and its operations to `kernel.write` |
| `write_batch` | Write | Up to 50 writes in order, each its own `kernel.write`; refs carry across ([ADR 0023](docs/decisions/0023-batch-writes.md)) |
| `lookup_entities` | Read | Ranked resolution candidates: identity keys, normalised names, trigrams, embeddings |
| `get_schema_slice` | Read | The kinds, edges and rules most relevant to a passage |
| `query_graph` | Read | Read-only Cypher, with belief status, `valid_at` and `known_at_offset` |
| `query_log` | Read | Log entries by source, agent, node, edge, time or offset |
| `ingest_source` | Write | Stores text or Markdown, chunks it with spans, skips known content; seals what is about people (`subjects`) so it can be erased |
| `cite` | Write | Records which assertions each answer sentence relied on |

A write:

```json
{"claim": {"text": "Sam took over invoice approval from Dana in March",
           "source": "chk_01K..._0002", "basis": "reported", "modality": "descriptive"},
 "read_at_offset": 41,
 "ops": [{"op": "assert", "edge": "implements", "from": "agt_01K...", "to": "ent_01K...",
          "valid_from": "2026-03-01"},
         {"op": "assert", "edge_id": "edg_01K...", "valid_to": "2026-03-01"}]}
```

Operations: `create`, `assert`, `link`, `unlink`, `promote`, `transition`, `redact`.
Rejections are RFC 9457 problem documents naming the broken rule:

```json
{"type": "urn:wmk:rule:cardinality", "title": "Single-valued edge conflict", "status": 409,
 "detail": "ent_01K... already has implements from agt_01K... for 2026-03-01T00:00:00Z onward; ...",
 "rule": "bpm.approver_cardinality", "candidates": [], "conflicting_edges": ["edg_01K..."]}
```

## Layout

| Path | Contents |
| --- | --- |
| `kernel/sql/` | The kernel, applied in order: sources, chunks, data keys and the erasure ledger, log, claims, assertions, cites, ontology, graph and AGE mirror, belief, resolution, projection, `kernel.write`, `kernel.ingest_source`, `kernel.cite`, read helpers, `kernel.erase`, governance and `kernel.decide`, roles; operator tools beside it (`kernel/token.py` mints sign-in tokens) |
| `gateway/` | The MCP server (official Python SDK): tools, RFC 9457 problems (`problems.py`), OTel names (`otel.py`) |
| `skills/` | Agent Skills: `core` (read, extract, write, cite) and `interview` (consent, gap queries, follow-ups) |
| `packs/` | Self-contained packs, each with its ontology (`schema.yaml`, `rules.yaml`), skill, tests, fixtures and servers ([writing a pack](docs/packs.md)): `research` (papers, per-paper findings, evidence queries, a paper-source server; [ADR 0013](docs/decisions/0013-research-findings-are-claims.md)), `software` (repositories, packages, images, services, stacks, pipelines and the self boundary, with a repository adapter, Docker observation and a forge adapter; [ADR 0017](docs/decisions/0017-software-pack-and-the-self-boundary.md), [0028](docs/decisions/0028-what-runs-observed.md), [0031](docs/decisions/0031-the-forge-as-evidence.md)), `process` (processes as told, written and done, with an event-log adapter; [ADR 0027](docs/decisions/0027-process-pack-and-event-logs.md)) and `bpm-reference` (the kernel's toy business-process test pack) |
| `adapter-kit/` | What the packs' deterministic adapters share: a plan of sources and claims, played through the gateway as an MCP client, and rendered as an eval fixture script |
| `profiles/` | ACP profiles `interactive.yaml` and `eval.yaml` |
| `evals/` | Fixtures with expected graphs, the resolution set, the eval runners, the replay check, the seeder |
| `tests/` | Unit, SQL, invariant and regression tests; the shared test kit is `kernel/testing.py` |
| `ui/` | The explorer: Alpine.js pages in `site/`, the Caddy config, the smoke check |
| `db/`, `docker-compose.yml` | The database image and the stack |

## Development

Python 3.12 with [uv](https://docs.astral.sh/uv/); Docker for the database.

| Command | Does |
| --- | --- |
| `make up` / `make down` | Start the stack / stop it and remove volumes |
| `make test` | Unit, SQL and regression tests (starts the database) |
| `make eval` | Every fixture through the eval profile (precision and recall for entities and edges), then the resolution set (auto-band precision and recall, candidate recall, clean new names) |
| `make replay` | Rebuild `$WMK_DATABASE` (default `wmk`) from its log and diff; a non-empty diff fails |
| `make erase-scope SUBJECT=agt_…` | What erasing a person would destroy, and what keys cannot reach (claims in the clear about them) |
| `make erase SUBJECT=agt_… REQUESTED_BY=… APPROVED_BY=… YES=1` | Destroy the person's keys, re-project, record the erasure, compact the tables; for operators ([ADR 0022](docs/decisions/0022-erasing-personal-data.md)) |
| `make live` | A real harness on a fresh stack: headless Claude Code, the skills and the gateway map a document and a five-turn interview (`northwind`, about US$3), or with `SCENARIO=research` on `WMK_PROFILE=eval`, three papers scored against the research fixture (about US$2); each answers with citations, then replays. Needs the `claude` CLI and model access; not in CI |
| `make annotated` | Extraction measured against annotators we are not: a real harness maps 30 SciFact abstracts blind, then judges a claim against each from the graph alone. Reports verdict accuracy, evidence capture, rationale precision and recall and calibration, with 95% intervals (about US$15 with Sonnet; `MODEL=` picks the model; results in [evals/annotated/RESULTS.md](evals/annotated/RESULTS.md)). Not in CI |
| `make papers-smoke` | One live lookup per paper source; needs network access to arXiv, Crossref and OpenAlex |
| `make northwind` | Northwind's purchase requests: the SOP and interviews (the `northwind-views` fixture, unless `make seed` played it), the log mapped onto the same nodes and checked against them, and the discovery report: where the three views disagree and what to automate first, each sentence with its sources |
| `make map-self` | Map this repository into the running stack with the software pack's adapter, with the kernel's self boundary (`SELF=` names the system) |
| `make observe-self` | Capture the running stack from Docker, observe it and check it for drift against what `map-self` declared (`PROJECT=` names the Compose project) |
| `make forge-self` | Capture this repository's pull requests from GitHub, map them, and audit its changes and deployments for approval (after `map-self` and `observe-self`; `FORGE_REPO=` names another repository, `FORGE_CAPTURE=` replays a recorded capture without the network) |
| `make up-ui` / `make ui-smoke` | Start the explorer / check that writes are refused, that every page loads in headless Chromium and, with `WMK_JWT_SECRET` set, that a minted token signs in and approves an open proposal (after `make seed`) |
| `make token EMAIL=… [NAME=…]` | Mint a sign-in token for one person with `WMK_JWT_SECRET`; for operators ([ADR 0030](docs/decisions/0030-signing-in-to-decide.md)) |
| `uv run python -m kernel.packs check` | Validate every pack's manifests ([ADR 0015](docs/decisions/0015-packs-declare-their-ontology.md)) |
| `make lint` | `ruff check` and `ruff format --check` |

Tests and evals create throwaway databases through `WMK_ADMIN_DSN` (default
`postgresql://postgres:postgres@localhost:5432/postgres`).

### Configuration

| Variable | Default | Used by |
| --- | --- | --- |
| `WMK_DB_PASSWORD`, `WMK_WRITER_PASSWORD`, `WMK_READER_PASSWORD`, `WMK_API_PASSWORD` | `postgres`, `writer`, `reader`, `api` | compose |
| `WMK_PROFILE` | `interactive` | compose: which profile the gateway runs |
| `WMK_PACKS` | every pack | compose and `kernel.packs install`: which packs the database gets |
| `WMK_WRITER_DSN`, `WMK_READER_DSN` | set by compose | gateway |
| `WMK_TRANSPORT`, `WMK_HOST`, `WMK_PORT` | `streamable-http`, `0.0.0.0`, `8000` | gateway |
| `WMK_EMBEDDING_URL`, `WMK_EMBEDDING_MODEL`, `WMK_EMBEDDING_API_KEY` | unset | gateway: optional OpenAI-compatible embeddings |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | gateway: export spans and metrics |
| `WMK_PAPERS_MAILTO`, `WMK_PAPERS_OPENALEX_API_KEY` | unset | papers: contact email for the APIs; OpenAlex search |
| `WMK_PAPERS_GROBID_URL` | `http://grobid:8070` in compose | papers: the GROBID service that parses PDFs for `get_full_text` |
| `WMK_UI_PORT` | `8080` | compose: the explorer's port on localhost |
| `WMK_JWT_SECRET` | unset | compose (the explorer's API only) and `make token`: signs and verifies sign-in tokens; unset, the explorer only reads |
| `WMK_REVISION` | the checked-out commit, set by `make` | compose: the revision label on the images it builds, so observed deployments know their commit |
| `GITHUB_TOKEN` | unset | `wmk-software forge capture`: optional, read-only; raises GitHub's rate limit |

## Guarantees

- One write path: the gateway's writer role may only execute `kernel.write`,
  `kernel.ingest_source` and `kernel.cite`; its reader role may only select. Only an
  operator's `kernel_eraser` role may call `kernel.erase`.
- Personal data is sealed when written: sources about people, the claims drawn from them and
  the fields of human agents are encrypted under per-subject keys. Erasing a person destroys
  their keys; the log is untouched, everything they sealed reads `[erased]`, and replay
  reproduces the erased graph ([ADR 0022](docs/decisions/0022-erasing-personal-data.md)).
- Agents never decide: only a signed-in person approves or rejects a proposal, through
  `kernel.decide` as the `kernel_approver` role, which no gateway login holds. Nobody
  decides on their own proposal or on a system they are part of. Instruments (what judges
  changes) need two people. Proposals and hypotheses state no facts, so belief never counts
  them ([ADR 0029](docs/decisions/0029-building-the-approval-channel.md)). The explorer's
  API takes that role only for a request carrying a token signed with a secret the gateway
  never holds ([ADR 0030](docs/decisions/0030-signing-in-to-decide.md)).
- The log tables refuse UPDATE, DELETE and TRUNCATE for every role.
- Projection makes no clock, random or network calls; CI replays the log and diffs.
- No model calls in the database or in a transaction: embeddings come from the gateway.
- Belief is a pure function of assertions: each source counts once and so does each
  origin, the people a source comes from
  ([ADR 0021](docs/decisions/0021-belief-v2-counts-origins.md)); no decay; conflicts
  between sources are shown as contested, never overwritten.
- Telemetry carries IDs, never claim text, source content or message content.
- A reported claim quotes the words of its source it rests on; the kernel finds them and
  records their span, or refuses the claim. A quote proves the words exist, not that they
  entail the claim.
- A source read in an extraction run is read as a whole: closing the run retracts what an
  older run over the same source found and this one did not
  ([ADR 0020](docs/decisions/0020-quotes-and-extraction-runs.md)).

## Current limits

Research is the first product pack: papers are found with the paper-source server and
mapped from their abstracts by any MCP harness following the core and research skills.
The process pack's adapter maps event logs without people (`org:resource` is dropped) and
checks decisions one gateway deep. The software pack maps what a repository declares and observes
what runs on Docker (other runtimes are later readers); its self
boundary is a set of claims, and changing the system stays with people. People decide on
the explorer's Proposals page, signed in with a token an operator mints for them; sign-in
through an identity provider (Forgejo, over OpenID Connect) is next. The forge adapter reads
GitHub only; a rebase merge shows only its last commit as merged, and a forge account and a
signed-in email stay different people until linked. Full text comes from open-access PDFs parsed by GROBID
([ADR 0025](docs/decisions/0025-full-text-through-grobid.md)). Not yet built: workers
([ADR 0024](docs/decisions/0024-extraction-workers.md), proposed: a script moving documents
from parser servers to the gateway with no model in between), observer runs, and the
pack registry (packs are installed from this repository by the stack's `packs` service; pack
servers run beside the gateway,
[ADR 0014](docs/decisions/0014-pack-servers-run-beside-the-gateway.md)). Erasure reaches
what was sealed: claims about a person that cite unsealed sources, entities named from
sealed ones, and copies in WAL archives and backups are outside it until redacted, retracted
or expired, and an operator role stands in for the approval channel
([ADR 0022](docs/decisions/0022-erasing-personal-data.md)). CI drives the eval profile with scripted extraction
([ADR 0008](docs/decisions/0008-eval-profile-runs-scripted-extraction.md)); `make live` and
`make annotated` run a real model.

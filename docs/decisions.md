# Decisions

Append-only. A decision is changed by adding a new entry that supersedes it, never by editing.
Status: **decided** (owner agreed), **adopted** (taken on recommendation while handing off — confirm
or overturn), **open** (needs the owner).

| ID | Decision | Status |
| --- | --- | --- |
| D-01 | **One Postgres is the core.** No Wikibase/MediaWiki runtime. Wikibase concepts (statements, qualifiers, references, ranks) live on in the claim model; Wikibase can be a publication target via a decoder later | decided |
| D-02 | **Five primitives**: entity, claim, artifact, run, change set. Terms, mappings, modules, slots, chains, profiles are entities + claims | decided |
| D-03 | **Three layers** — kernel (frozen v0), ABI (semver), distribution (modules). Kernel changes need evaluator evidence + owner decision | decided |
| D-04 | **Clearance, belief and reliability are separate.** Clearance is granted by people and enforced by Postgres; belief comes from a swappable belief model (first: corroboration counting); reliability feeds belief, never clearance | decided |
| D-05 | **All writes are change sets**, applied atomically by a kernel function that only a trigger can call. Auto and review lanes; vocabulary, mapping, module and profile changes are forced into review | decided |
| D-06 | **Erasure = redaction.** Content (object value, qualifiers, quotes, artifact bytes) is removed; identity, attribution, statement key and lineage stay; the redaction is its own recorded run by a steward. Invariant 2 is worded around it | adopted — confirm |
| D-07 | **Ontology tiers and audit verdicts** (spec §6): TOSCA 2.0 mirror owns system structure and replaces the earlier hand-made software properties; WoT TD owns runtime interfaces and transports; P-Plan owns plans; Wikidata IDs are link targets only; SOSA, UVL, ShEx, TOSCA workflows out of core | adopted — confirm |
| D-08 | **SQL storage, RDF meaning, graph as projection.** Export is N-Quads with scopes as named graphs; property-graph ergonomics through claim ids and qualifiers; graph engines only where algorithms pay off | decided |
| D-09 | **Bitemporal claims**: valid time + recorded time; status and belief are append-only histories | decided |
| D-10 | **Scopes replace "one wiki per trust boundary".** A claim's scope must be at least as restrictive as its inputs and referenced artifacts | decided |
| D-11 | **License posture**: permissive + LGPL libraries in kernel and default distribution; AGPL optional; Camunda 8 excluded | decided |
| D-12 | **Pin everything** (images by tag or digest, packages by version) and look up current docs instead of relying on memory | decided |
| D-13 | **Chain runner**: RuleGo-Server is the intended default; a Python bootstrap runner starts milestone 1; the evaluator's parity result (G4) decides. DBOS dropped from the default | decided |
| D-14 | **Hooks are harness-specific**; guarantees are enforced at the MCP/API boundary and in Postgres, never in hooks | decided |
| D-15 | **Milestone 1 = the system models itself** and hands its entrypoint to RuleGo-Server. The build-and-deploy BPMN loop is milestone 2 | decided |
| D-16 | **Default MCP set** (docs/mcp-servers.md): kernel-mcp, git (≥ 2026.1.14), filesystem, fetch, GitHub (read-only). Generic SQL MCP servers and the Memory reference server are excluded | adopted — confirm |
| D-17 | **Validators and reviewers need scope clearance** to see and act on change sets in that scope | adopted |
| D-18 | **Any agent may mint entity identifiers**; identifiers carry no meaning without claims | adopted |
| D-19 | **Vocabulary packs and module installs run as migrations** (`kk_owner`, `kernel.install_ops`): attributed and recorded like any change set, reviewed as code rather than in the in-app review lane | adopted — confirm |
| D-20 | **kernel-mcp holds no database role**; it forwards each caller's bearer token so RLS and attribution apply per agent | adopted |
| D-21 | **RuleGo-Server's MCP mode does not carry caller identity** (T1 spike, v0.38.0, docs/spikes/T1-rulego-mcp-identity.md): a chain tool receives only the tool arguments. Agent-facing tools stay in kernel-mcp; RuleGo runs system chains under module identities; only allow-listed MCP groups are routed, never the default endpoint. Closes O-7 | adopted — confirm |
| D-22 | **Default modules install on first database start** (`deploy/postgres/020_modules.sh` runs each `modules/*/migration.sql`: role + agent row; clearance via the default scope grants to `kk_agent`). Images pinned: Postgres 17.11-bookworm (pgvector 0.8.7, pg_cron 1.6.8), PostgREST v16.4, Caddy 2.11.7-alpine. kernel-mcp gets no role, per D-20, although its manifest names `kkm_kernel_mcp` | adopted |
| D-23 | **Packs install as a generated, committed migration**: `python -m pack_loader` writes `modules/pack-loader/packs.sql` (one `install_ops` per local pack and one for the slot catalog, key `pack:<id>:<version>`), applied on first start by `deploy/postgres/030_packs.sh`; CI fails if it is stale. Slot IRIs are `urn:kk:slot:<id>`. References are `file:<path>#sha256=<hash>` (no line spans yet). Upstream vocabularies are not fetched yet | adopted |
| D-24 | **Validator** (T4): the `validator-shacl` migration adds `api.validation_queue` (submitted change sets with ops, `security_invoker` so RLS applies); `api.change_sets` stays as is. The service is HTTP only (`POST /validate` returns a verdict and writes nothing); fetching and recording are the chains' job (T5). Content shapes see the `rdf:type` claims of referenced entities as context; only violations on the change set's own nodes count. Eval set `eval/validator` (14 cases) | adopted |

## Open

| ID | Question | Default until decided |
| --- | --- | --- |
| O-1 | Project name ("Knowledge Kernel" is a working name) | keep the working name |
| O-2 | Register the `https://w3id.org/knowledge-kernel/` namespace (or choose another) before the first public export | placeholder IRIs |
| O-3 | Hosting beyond local Docker/Podman | local only |
| O-4 | Models for extraction, agents and the small-model System 1 engine | whatever the harness uses; record per run |
| O-5 | Belief thresholds that move claims to preferred / deprecated | no automatic status changes from belief in milestone 1 |
| O-6 | Identity provider for people (OIDC at Caddy) | static JWTs for local use |
| O-7 | Whether RuleGo-Server's MCP mode can carry caller identity (milestone 1, T1) | agent-facing tools in kernel-mcp |

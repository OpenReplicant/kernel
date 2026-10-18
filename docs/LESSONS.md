# Lessons

What building milestone 1 taught, kept so the next session doesn't relearn it. Facts about external
tools here were checked by experiment (details in `docs/spikes/` and the decisions they cite).

## External tools, as they actually behave

- **RuleGo-Server** is the `server/` module of github.com/rulego/rulego (no separate repository, no
  published image; gitee unreachable). v0.38.0 = commit `1378763`; the `server/` module has no tag
  of its own, so Go pins it by pseudo-version (`modules/chain-runner-rulego/go.mod`).
- RuleGo runs **JavaScript, Lua and Python** (`x/python`, rulego-components, `with_extend` or a
  blank import) and whitelisted commands (`exec`). An early claim that it runs "JavaScript only" was
  wrong (D-33): check the component list before calling a step unmappable.
- RuleGo's MCP mode does **not** pass the caller's identity into a chain (D-21); its default MCP
  endpoint always exposes chain-editing tools and takes the API key in the URL path.
- RuleGo has **no Postgres LISTEN endpoint**; ours is `endpoint/pgNotify` (D-35). Endpoint configs
  are **not** run through `${global.X}` substitution; resolve them in `Init`.
- RuleGo's guide pages name interfaces but not signatures; the sources are the reference
  (`modules/chain-runner-rulego/README.md` has the how-to). Model endpoint: rulego-components
  `endpoint/redis`.
- RuleGo routing labels are `Success`/`Failure`/`True`/`False`; `for` with `do: chain:<id>`, mode 1
  collects results into an array; embedded `schedule` endpoints take 6-field cron (seconds first).
- **MCP Python SDK 2.3.0**: `MCPServer` (not `FastMCP`); `Context.headers` gives HTTP headers (never
  treat them as identity: forward the bearer and let PostgREST verify it); only `ToolError` messages
  reach the client; result fields are snake_case (`is_error`, `structured_content`); handshake
  clients negotiate 2025-11-25, the SDK's latest is 2026-07-28.
- A plain **GET on a streamable-HTTP MCP endpoint opens an SSE stream that never ends**: don't use it
  as a readiness probe (use `curl -m 1` or a health route).
- **PostgREST** filters: `in.("a","b")` with quoted IRIs; JSON paths like `object_value->>@value`
  work; empty bearer → 401 when `kk_anon` has no grants.
- **gitleaks** v8: `gitleaks dir <path> --redact -f json -r <file> --exit-code 0`; the binary is
  static and can be copied out of its image.
- Pick dependency versions at least ~2 weeks old (Cytoscape 3.34.4 was a day old; 3.34.3 was used),
  and check npm tarballs against the registry's integrity hash before vendoring.

## Kernel and design

- `run.plan_step` must reference an existing entity: chain and step IRIs are registered at install
  (`chains:identifiers:<hash>` in `packs.sql`).
- `api.change_sets` has no ops and `api.claims` no references: modules add `security_invoker` views
  in their migrations (`api.validation_queue`, `api.claim_references`); kernel RLS still applies.
- Module, chain, vocabulary and TOSCA node-type typings are **forced into the review lane**: a person
  must approve (`scripts/review.py`). Approve by id, not "everything from agent X" (that once
  approved a test module).
- Watermark polling loses work (a returned-but-lost change set left 600 claims unscored). Prefer
  events (D-32) and make every consumer able to recover what it missed (the belief service rescans
  unscored claims).
- Decoders and views that serve people must run with the **viewer's** token (graph decoder, kernel
  MCP), not the module's, or the view exceeds the viewer's clearance.
- Single-valued properties are typed `owl:FunctionalProperty` in the packs; a version bump of a pack
  reinstalls all of it (no diffing yet).
- Supersede, don't edit: new value + `set_status deprecated superseded replaced_by` (decoder claims).

- An empty libpq connection string is not "no connection": `psycopg.connect("")` connects with the PG*
  variables (a superuser in the dev sandbox). Check for a missing URL before connecting.
- A URL built from a socket connection (`host=/var/run/postgresql`) fails under peer auth for any other
  role: write TCP addresses into logins other processes will use (`kk up --db-host`).
- Claude Code expands `${VAR}` in `.mcp.json` (command, args, env), but its docs do not say a settings
  file's `env` reaches MCP servers; keep secrets in a file the server reads (`KK_LOGIN_FILE`).

- A claim in a scope the validator cannot read is never applied: services need clearance on every scope
  they serve (validator, belief, recall index), granted like anyone's.
- BM25 statistics kept per scope make a small scope's rare words worthless: sum them over the scopes
  the reader may read (row-level security does the filtering).
- Recall quality decides whether the kernel helps at all: with plain full-text search a good model did
  worse with the kernel than alone (SciFact, D-59); with BM25 it did much better (D-60). Measure recall
  before building on it.
- A CURIE in a change set expands only with a prefix the kernel knows (`kernel.prefix`: rdf, rdfs, owl,
  dcterms, prov, …); `cito:supports` is refused as "not a known term". Pack vocabularies without a
  registered prefix go in as full IRIs (D-62).
- A model must never see an eval item's key or path when it names the answer: RCAEval's case paths
  (`RE2-TT/ts-auth-service_cpu/1`) gave Haiku the root cause, scoring 0.99. Give items opaque keys,
  keep the map elsewhere, shuffle (D-65). And clear a benchmark's result folder between runs if its
  scorer reads every file there (RCAEval's `output/results`).
- Tune on SciFact's train split, report on dev: the published comparisons are on dev, and a choice made
  by looking at dev results inflates them. On dev, the prompt that won on train did not pay (D-64).
- A search path must never wait on a model download: harness subprocesses get a stripped environment
  (no proxy CA), and fastembed retries for ~40 s before failing. Queries load the model from the local
  cache only; the worker downloads it (D-61).
- A cross-encoder must read what a document says, not one claim of it: re-ranking SciFact claim by
  claim (a title alone, an abstract alone) lost to no re-ranking (0.719 vs 0.723); per subject on the
  recalled claims gained a little (0.731); per subject on all its text claims gained most (0.747, D-66).
  And hybrid scores are min-max normalised over the candidates, so the order depends on how many are
  recalled: compare runs at the same limit.
- Two claims with the same content from one agent are one claim to `kk import` (it merges by content,
  keeping one reference set): give each source file's claims their own subject when they would repeat
  (a requirements file requires its pins; it is part of the package beside it, D-68).
- Free model endpoints are rate-limited upstream per model (429 from a shared pool even with quota
  left); probe a few and record which model answered.

## Tests

- Gate checks that read a live system must tolerate other modules writing meanwhile: assert "every
  claim older than 10 s has a belief", not "zero unscored right now".
- A green eval proves little until it fails when the logic is broken: mutation-check evals (disable
  SHACL, count copies naively) once when you write them.
- Ask the real runtime, not just unit tests: compiled RuleGo chains were loaded into RuleGo-Server
  and their HTTP calls recorded; the browser check of the UI runs in headless Chromium.

## The cloud sandbox (Claude Code on the web)

- The container restarts without warning: Docker and Postgres stop, `/tmp` files survive. Restore
  with the steps in `.claude/skills/kernel-dev/SKILL.md`. After a restart Docker may fail with
  "timeout waiting for containerd": remove `/var/run/docker/containerd/containerd.pid` first.
- Outbound HTTPS goes through a TLS-intercepting proxy: pass its CA into image builds as a build
  secret (`KK_BUILD_CA=/root/.ccr/ca-bundle.crt sh scripts/smoke_stack.sh`); never disable TLS checks.
- Docker Hub rate-limits anonymous pulls (429): retry after a pause.
- `pkill -f <pattern>` matches the calling shell's own command line and kills it (exit 144); stop
  processes by `/proc/<pid>/exe` instead.
- Local Postgres uses peer auth: run `psql` and `kernel/schema/tests/run.sh` as the `postgres` user.

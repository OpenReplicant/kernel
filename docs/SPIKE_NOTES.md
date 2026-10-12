# Spike notes (S0)

Facts checked by experiment (or by reading source, where it says so), with how they were
obtained. Code is in `spike/` (throwaway); the gate is `spike/test_gate.py`.

## Result

**Gate passed** (2026-10-12): a Cypher write fires a trigger → outbox row → bridge → RuleGo
chain → a Python step reads the graph (vector + Cypher in one query) and writes an edge back;
a vector + graph query returns the expected vertex through the HNSW index; no outbox row is
lost while the bridge or RuleGo is down. AgensGraph works for both pgvector and triggers, so
the Apache AGE fallback is not needed.

```sh
spike/run.sh
DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres PATH=$PWD/.venv/bin:$PATH \
  pytest -v spike/test_gate.py
# 4 passed (5 clean runs back to back)
```

## 1. AgensGraph

- **Image:** `skaiworldwide/agensgraph:latest` pulls directly from Docker Hub in a cloud
  session (no mirror needed). Pinned by digest
  `sha256:1dccd703df570e20980693fe1407ec3af887ece29892bb7d69e5cd5a1176a4d7` (created
  2026-09-18): **AgensGraph v2.18.4.0, PostgreSQL 18.4, Alpine 3.24 (musl)**, built without
  LLVM, with plpython3u. `version()` reports `PostgreSQL 18.4 (AgensGraph 2.18.4.0)`.
- Other tags (2026-09-18): `v2.18.4.0-{alpine3.23,alpine3.24,bookworm,trixie,rocky9,rocky10}`
  and `v2.18.6.0-rc1-*`. Recipes: github.com/skaiworldwide-oss/agensgraph-docker. The source
  moved from bitnine-oss to github.com/skaiworldwide-oss/agensgraph.
- **Superuser is `agens`**, not `postgres`; the default database is `postgres`.
  `POSTGRES_HOST_AUTH_METHOD=trust` works as in the Postgres image.
- Host `psql` 16 can query it; its `\l` fails on the PG18 catalog (use SQL instead).
- A vertex label is a table `<graph>.<label>` (`id graphid`, `properties jsonb`); an edge
  label adds `start`, `"end"`. Labels inherit from `ag_vertex` / `ag_edge`. Element ids read
  as text `3.4` (label id . row id) and cast back with `'3.4'::graphid`.
- Cypher writes report as `UPDATE n` in the command tag.
- **Cypher takes bound parameters** (`WHERE id(d) = $1::graphid`, psycopg `%s`), but a
  parameter used **inside a property map** (`CREATE (d {source: $1})`) is typed `jsonb`:
  a text value fails with `invalid input syntax for type json`. Pass `Jsonb(value)`.

## 2. pgvector

- **Not bundled.** The image has no `vector` extension (stock contrib plus `meta`); the docker
  recipes never mention it. A web search found nothing official.
- **Expected as an external extension** (from the source): the docs say any Postgres extension
  works from a graph query, "which is how vector and full text search work"; regression tests
  note vector/HNSW coverage "needs the pgvector extension, which is not present in a plain
  `make check`".
- **Build:** distro package mirrors (dl-cdn.alpinelinux.org, deb.debian.org,
  apt.postgresql.org) are blocked from cloud sessions, so `apk add` fails (HTTP 403). Instead,
  `spike/agensgraph/Dockerfile` compiles **pgvector v0.8.7** (commit `f37c13f`) with gcc in
  `mirror.gcr.io/library/rust:1-alpine` (Alpine 3.24, gcc 15.2, musl; Rust itself unused)
  against the AgensGraph image's server headers, then copies `vector.so`, the control file
  and the SQL into the AgensGraph image. Flags are pgvector's with `OPTFLAGS=""` (portable).
  ~10 s compile. `CREATE EXTENSION vector` → 0.8.7.
- **Vectors on vertices, the AgensGraph way:** `CREATE VLABEL doc (embedding vector(3) GENERATED)`
  **promotes** the property to a stored generated column
  `((properties ->> 'embedding')::vector(3))`. Cypher writes the property as a plain list
  (`{embedding: [1,0,0]}`); `d.embedding` in Cypher resolves to the typed column
  (GUC `enable_property_promotion`, default on).
- `CREATE PROPERTY INDEX doc_emb ON doc USING hnsw (embedding vector_l2_ops)` makes an HNSW
  index on that column.
- **Vector + graph in one query:**
  `MATCH (d:doc)-[:about]->(t:topic {name: 'vertical'}) RETURN d.name ORDER BY d.embedding <-> '[0.1,0.1,1]'::vector LIMIT 1`
  returns the expected vertex. `MATCH (d:doc) RETURN d.name ORDER BY d.embedding <-> $v LIMIT 2`
  plans as `Index Scan using doc_emb` (checked with `enable_seqscan = off` on 3 rows).

## 3. Triggers and the outbox

`spike/sql/02_outbox.sql`: a plpgsql `AFTER INSERT OR UPDATE OR DELETE … FOR EACH ROW`
trigger on the label tables writes an outbox row (graph, label, op, element id, old/new
properties, causation, depth, txid) and `pg_notify('outbox', seq)`.

- Fires **per row** on Cypher `CREATE`, `SET` and `DELETE`, for **vertex and edge labels**
  alike (a 2-vertex `CREATE` gave 2 rows; `SET` gave old and new property maps).
- `DETACH DELETE` of a vertex fires the vertex trigger and the edge trigger for each edge.
- The outbox row is in **the same transaction** as the write (`txid` matches); `NOTIFY` is
  delivered only on commit; a rolled-back write leaves no row and no notification.
- **Sequence gaps:** the rolled-back write still consumed a `seq`. Consumers must track
  `done_at` per row, never "last seq seen". (Under concurrency, seq order is not commit order
  either.)
- **Causation and depth** travel as transaction-local settings
  (`set_config('kernel.causation', …, true)`, `kernel.depth`), which the trigger reads with
  `current_setting(…, true)`. A step that writes sets them, so the rows its writes cause carry
  `causation = outbox:<seq>`, `depth = n + 1`. This is the hook for the loop guard (S1).

## 4. Outbox → RuleGo

- **RuleGo has no Postgres LISTEN endpoint** (read in rulego `cafafc3` and rulego-components
  `1600be6`, 2026-10-12: endpoints are rest, mqtt, net, websocket, schedule, plus
  kafka/nats/redis/… in components). The only Postgres support is the `x/dbClient` node.
- **Chosen: a small bridge** (`spike/bridge.py`, ~70 lines): LISTEN `outbox`; on start, on
  each notification and every `POLL` s, lock the oldest undone row
  (`FOR UPDATE SKIP LOCKED`), `POST /api/v1/rules/{chain}/execute/GRAPH_CHANGE` (synchronous),
  and set `done_at` in the same transaction; a failure records `attempts` and `last_error`
  and the row is retried.
- **Delivery is at-least-once**: a crash after the chain ran but before the commit
  re-delivers, so steps must be idempotent (the spike step checks before it writes).
- **No loss** (tests): 5 writes while the bridge is down stay pending and are all delivered
  when it restarts, without a new NOTIFY; with RuleGo down the row fails (attempts ≥ 1) and is
  delivered after RuleGo restarts.
- A failing row blocks the rows behind it (head-of-line, by `ORDER BY seq`). Fine for the
  spike; S1 needs a policy (retry limit, dead-letter, per-element ordering).
- The polling alternative (schedule endpoint + `x/dbClient`) was not built: it needs claim
  and mark logic in chain nodes and adds latency; the bridge was simpler.

## 5. RuleGo and a Python step

- Server: the second iteration's module (`spike/rulego/`: RuleGo-Server v0.38.0 +
  rulego-components `x/python` at `fbbef88`), built on the host with Go 1.24.7 and
  `GOTOOLCHAIN=auto` (fetches Go 1.25), run on the host so `x/python` uses the repo venv
  (`.venv` first on `PATH`; the node uses `python3` from `PATH`).
- `spike/steps/classify.py` on `x/python` reads `DATABASE_URL` from the server's environment,
  connects with psycopg 3.3.6, reads the graph (nearest classified neighbour by vector, in a
  Cypher match) and writes `(d)-[:about {method: 'inferred', source: 'outbox:<seq>'}]->(t)`.
- **Changing a `.py` step needs a chain redeploy:** the node loads the script when the chain
  is deployed (an edited step kept failing with the old code until `POST /api/v1/rules/{id}`).
- Hot-deployed chains persist to `data_dir/workflows/admin/rules/<id>.json` and are reloaded
  on restart (the RuleGo-down test relies on it).
- A failing step returns HTTP 400 to the bridge with the Python error, recorded in
  `last_error`.

## Cloud-session notes

- `pkill -f <pattern>` from a Bash tool call can kill the calling shell when the command
  text contains the pattern; match by process name (`pkill -x rulego-server`) or full path.
- A killed `rulego-server` can linger as a zombie; `pgrep` matches it, so wait for the port
  rather than the process.
- One gate run failed early, before the readiness check was added: RuleGo was not
  serving during the tests. Not reproduced since; `test_gate.py` now checks RuleGo is up
  before each test.

## Open (for S1 or later)

- Embedding model and where embeddings are computed (design question 3), untouched here.
- Retry and dead-letter policy for the bridge; whether the bridge becomes a Go endpoint
  compiled into the server.
- Outbox rows for labels created later: triggers are per label table, so the module loader
  must add them when it installs an ontology (or use an event trigger).

## Facts checked for S1 (2026-10-12)

Tried against `kernel-agensgraph` (same image as above) while building the core:

- **A `timestamptz` can't be a promoted column:** `CREATE VLABEL c (recorded_at timestamptz GENERATED)`
  fails with "generation expression is not immutable" (text → timestamptz is only stable). The
  kernel stores times as fixed-width ISO-8601 UTC strings, which order correctly as text and as
  jsonb strings.
- **Labels fold to lower case** (`CREATE VLABEL Thing` makes `thing`; `:Thing` and `:thing` match).
- **Label inheritance works with Cypher:** `CREATE VLABEL note INHERITS (thing)`;
  `MATCH (t:thing)` returns notes too, with `label(t)` = `note`. Typed columns and `INHERITS`
  combine: `CREATE VLABEL note (embedding vector(3) GENERATED) INHERITS (thing)`.
- `CREATE GRAPH|VLABEL|ELABEL IF NOT EXISTS` and `CREATE PROPERTY INDEX IF NOT EXISTS` work.
- **BEFORE row triggers apply to Cypher writes**: they can rewrite `NEW.properties` (a forged
  `recorded_at` is overwritten) and refuse `SET`, `REMOVE`, `DELETE` and `DETACH DELETE` by
  raising.
- **Cypher `SET` drops keys whose value is null** from the property map (AgensGraph strips null
  keys on write unless `allow_null_properties`). A trigger comparing old and new maps must
  compare with nulls stripped, and the kernel never stores nulls.
- **Cypher silently creates a label it hasn't seen** (`CREATE (:typo)` runs an internal
  `CREATE VLABEL "typo"` through SPI, `parse_graph.c: createLabelIfNotExist`); there is no
  setting to turn this off. An **event trigger** on `ddl_command_end` sees it (as `CREATE SEQUENCE
  <graph>.typo_id_seq` first) and can abort it; the write then fails and no table is left.
  Unlabelled `CREATE ({...})` goes to the base table `ag_vertex`, where a BEFORE trigger can
  refuse it.
- **A whole property map can be one parameter:** `CREATE (n:note %s)` with a jsonb value, and
  `SET n += %s`. Unlabelled `MATCH (n) WHERE id(n) = '3.4'::graphid` finds a vertex of any label.
  A missing property reads as NULL (`n.x IS NULL`).
- **plpgsql and NULL:** `IF NOT (p->>'method') = ANY(...)` is NULL for a missing key, and `IF
  NULL` does not raise. The first trigger let a claim without `method` through; the tests caught
  it. Checks use `coalesce`.
- **RuleGo `x/python`:** chain-level `ruleChain.configuration.vars` reach `Process` as its fifth
  argument (`vars`); HTTP query parameters arrive in `metadata` (with `id` = the chain id).
- **RuleGo's `exec` node is on by default** with a whitelist of `cp, scp, mvn, npm, yarn, git,
  make, cmake, docker, kubectl, helm, ansible, puppet, pytest, python, python3, pip, go, java,
  dotnet, gcc, g++, ctest` (server `config.go` default). The kernel's config sets
  `cmd_white_list = kernel-exec-disabled`; an exec node then fails with "cmd not allowed".
- **YAML 1.1 reads a bare `on:` key as `true`** (PyYAML): the manifest uses `when:`.

## Facts checked for S2 (2026-10-12)

- **Schedule endpoint in a deployed chain works** on our server (v0.38.0): `metadata.endpoints`
  with `{"type": "endpoint/schedule", "routers": [{"from": {"path": "*/1 * * * * *"}, "to":
  {"path": "<chain>:<node>"}}]}` fires every second (cron with seconds); messages carry metadata
  `_triggerSource = endpoint/schedule` and empty data. Deleting the chain
  (`DELETE /api/v1/rules/<id>`) stops it. A hot-deployed chain keeps ticking until deleted, also
  across test sessions.
- **Schedule ticks overlap:** the cron fires on time whether or not the previous run finished.
  With several Python nodes per tick, runs bunched up and four ticks recorded the same decision
  within 90 ms. The control chain now writes decisions with a compare-and-set (`if_current`).
- **`for` node** (`components/common/for_node.go`): `{"range": "msg.rooms", "do":
  "chain:<id>", "mode": 0}` runs the sub-chain once per item, with the item as the message
  data. Modes: 0 ignore results, 1 merge into an array, 2 replace the message, 3 async.
- **`flow` node across modules' chains** (`{"targetId": "<chain>", "extend": true}`) and
  **`switch`** (`{"cases": [{"case": "msg.act == true", "then": "Act"}]}`, connection type
  `Act`) work as documented in `second_iteration`'s notes; an unmatched switch ends the branch.
- `curl -d '<json>'` without a JSON content type is accepted by the execute API.

## Facts checked for S3 (2026-10-12)

- **`claude -p` works in this cloud container**, from the shell and from an `x/python` step in
  RuleGo on the host (Claude Code 2.1.296): `claude -p --model haiku --tools "" --strict-mcp-config
  --setting-sources "" --no-session-persistence --system-prompt … --output-format json`, prompt
  on stdin, run in an empty directory, answered in 3–4 s as `claude-haiku-5-5`.
- **Its top-level `usage` undercounts; `modelUsage` matches the cost.** One call: `usage` said
  2 input / 4 output / 1245 cache-write tokens, `modelUsage["claude-haiku-5-5"]` said 1176
  input / 18 output / 1245 cache-write, and `total_cost_usd` was 0.0003756 =
  (1176 × 0.10 + 18 × 0.50 + 1245 × 0.20) / 10⁶, i.e. Haiku 5.5 at $0.10 / $0.50 per million
  with the cache write (`ephemeral_1h_input_tokens`) at 2× input. Tokens are summed from
  `modelUsage`; a missing cache-write price is charged at 2× input.
- The child `claude -p` reported the parent session's `session_id` (it inherits the session's
  environment); with `--no-session-persistence` nothing was observed to be written.
- **Anthropic SDK 1.13.0** reaches a local Messages-compatible server through `base_url` with
  the session's proxy variables set (`NO_PROXY` covers 127.0.0.1); so does `urllib` for the
  OpenAI-compatible kind.
- AgensGraph Cypher: `label(v)` returns a vertex's label name.
- psycopg's `with connect() as conn:` rolls back on an exception, so a record that must survive
  a failure (a refused or failed model call) is committed before the error is raised.

## Facts checked for S4 (2026-10-12)

- **This environment's Docker** (restarted by hand after a container restart): runtime `runc`
  1.5.1 only (no gVisor), seccomp's default profile, cgroup v1. These run flags all work:
  `--network=none --read-only --tmpfs /tmp --cap-drop=ALL --security-opt=no-new-privileges
  --cpus --memory --memory-swap --pids-limit --user=10001:10001 -v <dir>:/work:ro` on
  `mirror.gcr.io/library/python:3.12-slim` (no network: errno 101; root and `/work` read-only;
  `/tmp` writable; a 200 MB allocation under `--memory=64m` exits 137). A run takes 0.4–0.8 s.
- **The MCP Python SDK (`mcp` 2.3.0)**: `MCPServer` with `@server.tool()`; `Client(StdioServerParameters(...))`
  or `Client("<url>")`. Its stdio client starts the server with only `HOME`, `LC_CTYPE`,
  `PATH`, `SHELL`, `TERM` from the caller's environment: a probe server run with
  `DATABASE_URL` and a fake secret set in the client saw neither. A tool's exception reaches the
  client as a generic "Error executing tool" unless it is a `ToolError`, whose text is passed on.
- **Container MCP servers** (read in source by a research subagent, not run):
  `ckreiling/mcp-server-docker` (v0.3.0) — `run_container` takes image, command, network,
  env, ports, volumes, auto_remove; no read-only, limits, user, cap_drop or tmpfs; detached;
  volumes accept any host path. `QuantGeekDev/docker-mcp` (stale since 2024-12) — no command,
  detached. Docker's catalog `docker` server — raw CLI args with the socket mounted.
  `manusa/podman-mcp-server` (v0.0.15) — `container_run(image, ports, env)`, no command,
  publishes all ports. `containers/kubernetes-mcp-server` (v0.0.67) — `pods_run` with no
  securityContext or resources; any manifest via `resources_create_or_update`.
  `Flux159/mcp-server-kubernetes` — manifests and raw kubectl. No public HashiCorp Nomad MCP
  server found; `kocierik/mcp-nomad` (v0.3.1) takes any job spec. None enforces isolation or
  returns an exit code from a finished run; hence our own server (docs/SANDBOX.md).
- **RuleGo's AI components** (read in source by a research subagent, not yet built or run):
  `github.com/rulego/rulego-components-ai`, enabled in upstream's server by build tag
  `with_ai` (a blank import of `rulego-components-ai/all`); the v0.38.0 server pins
  `v0.37.1-0.20260816034014-d5312a602f16`. Nodes: `ai/llm` (OpenAI-compatible: `url`, `key`,
  `model`, `systemPrompt`, `messages`, `params`; puts only the text in the message, dropping
  token usage and the model name), `ai/agent` (eino ReAct; tokens in metadata, model as
  configured), `ai/mcpClient` (`server`: an http(s) URL → streamable HTTP, anything else → a
  stdio command; `toolName`, `args`). mcp-go v0.44.0's stdio transport passes the **whole**
  environment of the RuleGo process to the server it starts. No container or docker node
  exists in rulego, rulego-components or rulego-components-ai.
- `claude -p` children leave directories named after their working directory under the
  session's temp root (`/tmp/claude-0/-tmp-kernel-claude-*/<parent session id>/scratchpad`),
  despite `--no-session-persistence`; they hold nothing else.

## Facts checked for S5 (2026-10-12)

- **`claude -p` as an agent with only our MCP server** (Claude Code 2.1.296, Haiku): with
  `--tools "" --strict-mcp-config --mcp-config '<json>' --allowedTools mcp__sandbox`, the init
  event lists exactly `["mcp__sandbox__sandbox_run"]`; Haiku called it, read the result and
  answered (2–3 turns, 7–12K tokens, 7–11 s). Without `--allowedTools` a tool needs a
  permission nobody can give in `-p` mode. `--output-format stream-json --verbose` gives the
  init event (tools, MCP server status), every assistant message (text, `tool_use`), every
  tool result (as a `user` message) and the result event (`subtype`, `num_turns`,
  `modelUsage`, `total_cost_usd`). `--max-turns` is accepted though `--help` doesn't list it.
  Asked to read `/etc/hostname`, Haiku used the sandbox and got the container's hostname. A
  real run hit the read-only `/work`, said so, and wrote to `/tmp` instead.
- **Claude Code starts stdio MCP servers with its whole environment**: a probe server listed
  158 variables, among them `GH_TOKEN`, `GITHUB_TOKEN`, `AWS_SECRET_ACCESS_KEY`, `HTTPS_PROXY`
  and a planted `SANDBOX_TEST_SECRET`. The `env` field of an MCP config adds variables, it
  doesn't replace them. Wrapping the command in `env -i PATH=… HOME=… PYTHONPATH=…` leaves the
  server with those (plus `LC_CTYPE`, which Python sets itself): checked by the S5 gate.
- **RuleGo's AI components build** with Go 1.25 (auto toolchain) from
  `rulego-components-ai@v0.37.1-0.20260816034014-d5312a602f16`: importing `…/all` fails on
  missing modules for its browser, skill and image tools; importing only `…/action` (`ai/llm`,
  `ai/createImage`) and `…/mcp` (`ai/mcpClient`) builds (binary 55 MB against 42 MB) and the
  server lists `ai/llm`, `ai/mcpClient`, `ai/createImage`.
- **`ai/llm`, run**: against the local fake's `/v1/chat/completions`, it sends `model`,
  `messages` (system prompt first), `max_tokens`, `temperature`, and also `top_p: 0.75` and
  `response_format: {type: text}` unasked; `${msg.text}` is substituted only when the message is
  JSON (an execute request without `Content-Type: application/json` gets the literal
  `${msg.text}`); the node's output is the reply text alone, as the source said.
- **`ai/mcpClient`, run**: with `server: http://127.0.0.1:8000/mcp` (our sandbox server over
  streamable HTTP) and `toolName: sandbox_run`, every call failed with `MCP tool not found:
  sandbox_run`, and the server logged no connection: the node connects in `Start()`, which
  neither `Init` nor `OnMsg` calls (in this version only an agent that uses it as a tool
  provider does).
- **LiteLLM 1.105.0 SDK**: `completion(model="openai/small-o", api_base=<fake>/v1)` posts to
  `/v1/chat/completions`; `model="anthropic/small-a", api_base=<fake>` posts to `/v1/messages`
  (system as a block list, content as blocks). Both return the server's model name and usage
  (`prompt_tokens`, `completion_tokens`), `finish_reason` `stop`, and no cost for models it
  doesn't know (`response_cost` None). Its exceptions derive from the `openai` package's
  `OpenAIError` (not from `litellm.exceptions.OpenAIError`, a different class). It fetches its
  price map from the network at import unless `LITELLM_LOCAL_MODEL_COST_MAP=True`.

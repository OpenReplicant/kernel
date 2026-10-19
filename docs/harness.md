# Dropping the kernel into an agent harness

The kernel is a Postgres database with the kernel schema plus a validator. Any harness that speaks MCP
can use it; how deeply depends on what you add (D-36):

| Level | Add | You get |
| --- | --- | --- |
| 1 | the MCP server | the agent reads, cites and submits claims under its own identity |
| 2 | + the skill file | the agent knows the conventions: attribution, references, evidence types, citing belief |
| 3 | + hooks | the harness recalls on its own: identity and a session run at start, related claims with every prompt, the run finished at the end |
| 4 | + modules | encoders, belief, further processors and decoders, a UI (`kk serve` runs the default ones) |

**The short way:** `kk up PROJECT` does sections 1–4 for one project folder (D-57): it starts the
compose stack, gives the owner and the project's agent their own logins (kept in
`~/.config/knowledge-kernel`), and merges the MCP entry, the skill and the hooks into the project
(`.mcp.json`, `.claude/skills/knowledge-kernel/`, `.claude/settings.local.json`). `--no-stack` uses the
kernel at `KK_ADMIN_URL` instead. The rest of this guide is what it does, by hand.

## 1. A database with the kernel

Either the compose stack (`deploy/compose.yaml`: Postgres and one image running `kk serve`), or any
Postgres 16+ you run yourself, set up with `kk` (`pip install -e .` from a checkout):

```sh
export KK_ADMIN_URL=postgresql://postgres:<password>@localhost/kk     # a superuser, for setup only
KK_AUTHENTICATOR_PASSWORD=<service secret> kk init
kk person add owner --steward      # prints {"role": "person_owner", "password": ..., "database_url": ...}
kk agent add claude-code           # prints the same for agent_claude_code
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk KK_JWT_SECRET=<32+ chars> kk serve &
```

`kk serve` runs the validator and the belief model (and MCP over HTTP); without the validator nothing
submitted is ever applied. `kk serve --only validator,belief` leaves MCP to the harness's stdio server.

Who holds which login (D-37):

| Login | Held by | Can act as |
| --- | --- | --- |
| `kk_authenticator` | services you run (`kk serve`) | any role granted to it, so never give it to an agent |
| `agent_<name>` (`kk agent add`) | that agent's harness | itself only |
| `person_<name>` (`kk person add`) | that person | itself only |

The kernel enforces clearance and attribution by the role. `scripts/smoke_kernel.sh` does all of this
in a throwaway database and checks it end to end.

## 2. The MCP server

Install once (any Python 3.12+ environment): `pip install ./modules/kernel-mcp`, or without a checkout
`uvx --from "git+https://github.com/OpenReplicant/kernel#subdirectory=modules/kernel-mcp" kernel-mcp`.
This gives two commands: `kernel-mcp` (the MCP server) and `kernel-mcp-hook` (the hooks).

stdio, one agent per harness. For Claude Code (`.mcp.json`; the same entry is in
`deploy/mcp/agents.mcp.json`):

```json
{"mcpServers": {"kernel": {
  "command": "kernel-mcp",
  "env": {"KK_DATABASE_URL": "postgresql://agent_claude_code:<agent secret>@localhost/kk"}}}}
```

Instead of the secret in `env`, `KK_LOGIN_FILE` may name a file holding `KK_DATABASE_URL=...` (what `kk up`
writes), so the project's files carry no secret. Each agent gets its own login and agent IRI (`kk agent add NAME`: role `agent_<name>`, IRI
`urn:kk:agent:<name>`); stdio refuses
a login that can switch roles. `KK_DATABASE_URL` (or `DATABASE_URL`) names it; the `KK_` name wins so
a project's own `DATABASE_URL` does not collide. With the compose stack, Postgres is on
`localhost:5432` and the agent's password is what `kk agent add` printed.

Shared server for several agents: `kk serve` (or `kernel-mcp --http`) with `KK_JWT_SECRET`; each
request sends `Authorization: Bearer <JWT>` whose `role` claim is the agent's role
(`kk token agent_claude_code`).

**Profiles (M6-T1).** Which tools an agent sees is chosen per login: `KK_PROFILE` in the MCP entry
(stdio) or the token's `profile` claim (`kk token agent_claude_code --profile memory`). `memory` shows four
tools: `remember`, `recall`, `explain` and `changes_since`; the briefings are also MCP resources
(`kernel://recall/{question}`, `kernel://changes`). `operator`, the default, shows every tool. `kk up`
writes `memory` unless given `--profile operator`. A profile keeps the agent's context small; it is not
a security boundary, since Postgres decides what each login may do.

## 3. The skill file

Copy `modules/kernel-mcp/SKILL.md` into the harness's skills folder (Claude Code:
`.claude/skills/knowledge-kernel/SKILL.md`). It tells the agent how to cite, what to record and how.

## 4. Hooks: recall and recording without asking

`kernel-mcp-hook` implements three hooks, and `kernel-mcp-cue` a fourth (PreToolUse: memories cued by the tool or the file about to be used, D-91; it reads no database); the Claude Code configuration is
`modules/kernel-mcp/hooks/claude-code.json` (merge its `hooks` into `.claude/settings.json`).

| Hook | Does |
| --- | --- |
| `session-start` | starts a kernel run for the session and tells the agent its identity and run id |
| `prompt` | recalls up to `KK_RECALL_LIMIT` (8) claims ranked by relevance to the prompt, with ids and belief |
| `session-end` | finishes the session's run |

Hooks inherit the harness's environment, so export `KK_DATABASE_URL` (the agent's own login) before
starting the harness. A hook never blocks the session: on any error it writes to stderr and exits 0.
Session runs are remembered in `KK_STATE_DIR` (default `~/.local/state/knowledge-kernel`). Other
harnesses call the same commands with their own hook JSON (`session_id`, `prompt`, `reason`).

## The UI

`kk serve` also serves the UI at `/ui/` (behind Caddy: http://127.0.0.1:8080/ui/). Sign in with your
name and the password `kk person add` printed (`kk up` prints the owner's), or paste a token
(`kk token person_<name>`, with `KK_JWT_SECRET` set) (D-74). On a public host name, add
`deploy/public.yaml` and Caddy serves HTTPS. Then: search and explain, the review queue, change
sets, the graph, and every view module. A view module is a folder with a manifest (runtime type
`view`, slot `ui`) and one script that calls `KK.register({id, title, render(el, kk)})` and renders with
`kk.call(tool, args)`; see `modules/view-modules` (D-44).

## Installing further modules

Modules in this repository's `modules/` folder install into a running kernel, each in its own
locked-down container (D-43):

```sh
KK_ADMIN_URL=postgresql://postgres:<password>@localhost:5432/kk kk module install modules/<name>
docker compose -f deploy/compose.yaml -f deploy/modules/<name>.yaml --env-file deploy/.env up -d
```

The module gets its own login (it acts only as itself), a read-only filesystem, no capabilities and a
network that reaches Postgres and nothing else, plus what the sandbox policy `deploy/policy.yaml` grants
it (D-55): the services it may reach, a route out, memory and process limits. A module that asks for
network the policy does not grant is refused; edit the policy to grant it.

## Moving or sharing claims

`kk export > kernel.nq` writes every claim with its provenance as N-Quads, one named graph per scope, in
standard vocabularies (PROV, Dublin Core, Web Annotation, DQV; D-45); `--scope` limits it. `kk import
kernel.nq` loads it into another kernel, keeping each claim's agent, scope, references, derivations
and status; claims already there are not added twice.

## Ingesting a repository

The self-encoder scans a repository into claims (module manifests, compose files, vocabularies, SQL),
running the ingest filter first so files with secrets are never stored, and superseding what changed
since the last scan:

```sh
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk kk scan --repo .   # gitleaks v8.30.1 on PATH
```

Run it from a git hook, a scheduler or the harness. Module and vocabulary claims wait in the review
lane until a person approves them.

## Reviewing

Typing new properties or classes goes to the review lane. A person approves with
`KK_DATABASE_URL=postgresql://person_owner:<owner secret>@localhost/kk kk review list`, then
`kk review approve <ids>`. `kk status` shows what is waiting.

# Knowledge Kernel

A knowledge management system where nobody has to write the knowledge down. It is captured from the
conversations, logs, documents and code where work already happens; every fact cites its source, shows how
sure we are, and is reviewed by a person before it counts (D-84).

- **For developers: an agent memory layer.** A small kernel that drops into any agent harness. One Postgres
  holds every claim, each attributed, referenced, belief-scored and scoped. Use it over MCP alone, or add the
  skill file, harness hooks and further modules (`docs/harness.md`).
- **For organisations: distributions.** A distribution is a preset bundle of modules, vocabularies, skills and
  views that does one job, installed as its own stack. The first: organisation and process maps from
  conversations.

- **Work with us:** `docs/offer.md`: paid pilots of process intelligence and the self-healing MAPE-K loop, memory for your agents, custom modules. Apache-2.0 (`LICENSE`).
- **How it works, in plain language:** `docs/architecture.md`
- **For a developer joining:** `docs/developer-guide.md` (the moving parts, the interfaces, where to plug in)
- **Specification:** `docs/spec.md` (kernel v0 is frozen)
- **Status:** milestones 1 (the kernel, `docs/milestone-1.md`), 2 (BPM and devops as modules and views, `docs/milestone-2.md`), 3 (your own data, cited, in minutes: `docs/milestone-3.md`) and 4 (a demo you can sell: `docs/milestone-4.md`, `kk demo`) are done; milestone 5 (maps from conversations, interactions and code: `docs/milestone-5.md`) built the interaction encoder and the interviewer; milestone 6 (agent memory without baggage: `docs/milestone-6.md`) is under way: the four-tool memory profile is in. What each module takes in and gives out: `docs/modules.md`.
- **Decisions and open questions:** `docs/decisions.md`
- **Working rules:** `CLAUDE.md`

## Quick start: connect your project

With Docker and Python 3.12+, from a checkout:

```sh
pip install -e .
kk up ~/code/my-project        # the kernel in Docker, your and the agent's logins, MCP + skill + hooks, and the project read in
cd ~/code/my-project && claude # approve the 'kernel' MCP server; the agent now recalls, cites and records
```

The agent sees four tools (the `memory` profile, M6-T1): `recall(question)` answers with a briefing in which
every line says who said it and how sure the kernel is, with a handle to cite; `remember(kind, text)` records
one sentence, such as a decision and its reason; `explain(handle)` shows the source; `changes_since()` says
what others changed. A line in its reply that starts with `Decision:` or `Remember:` is recorded from the
session transcript too. `kk up --profile operator` gives it every tool instead.

```text
Recalled for "which psycopg do we pin?" (who said it; belief 0-1; explain(#handle) for sources):
- decision: "We pin psycopg 3.3.6: 3.4 drops Python 3.11" (about psycopg) — Ana (person); belief 0.80 #5a32ce36
- knowledge-kernel · softwareRequirements · psycopg (version ==3.3.6) — repository encoder (agent); belief 0.90 #0c1d7e42
```

`kk up` prints the UI address (http://127.0.0.1:8080/ui/) and your sign-in (name and password) for reviewing. Logins
stay in `~/.config/knowledge-kernel`, never in the project; running it again changes nothing but what
changed in the project (D-57). It reads the project's packages, dependencies and documents in (D-68,
D-69); when the checkout moves on, the session tells the agent, and `kk up` again refreshes it.

## The process demo

```sh
pip install -e .
kk demo           # order-to-cash, bundled; or kk demo --bpi for BPI Challenge 2019 (1.6 M events)
```

It loads an event log, its documented BPMN model and the written procedure, and prints the UI address
with the owner's sign-in. The walkthrough (`docs/demo.md`) covers documented against actual, the
deviations worth reviewing with their evidence, a process model that learns from what people approve,
the model as BPMN, and an agent's cited answers (D-77).

## Run it by hand

One command, `kk`, sets up and runs a kernel (`pip install -e .` from a checkout, Python 3.12+):

```sh
export KK_ADMIN_URL=postgresql://postgres:<password>@localhost/kk   # a superuser, for setup only
KK_AUTHENTICATOR_PASSWORD=<service secret> kk init   # kernel, default modules, vocabulary packs
kk person add owner --steward                        # your own login (prints its password once)
kk agent add claude-code                             # the agent's own login, for its harness
DATABASE_URL=postgresql://kk_authenticator:<service secret>@localhost/kk KK_JWT_SECRET=<32+ chars> \
  kk serve                                           # MCP over HTTP + validator + belief model
```

| Command | Does |
| --- | --- |
| `kk up [PROJECT]` | all of the below for one project: the stack, the logins, the MCP entry, skill and hooks |
| `kk demo [--bpi]` | the process demo: `kk up` on a demo folder, then the event log, the BPMN model and the procedure (`docs/demo.md`) |
| `kk init` | installs kernel v0, every default module's migration and the vocabulary packs (once) |
| `kk person add NAME` / `kk agent add NAME` | an own login that can act only as itself (D-37) |
| `kk serve [--only mcp,validator,belief,recall,quotes]` | the running kernel: one process, or one container per part; the UI at `/ui`. Add `conformance,learner` for the process loop (D-50, D-76; the compose stack runs them). `quotes` checks extracted claims' quotes against their sources (artifact store and `file:` sources under the working directory, D-67). Add `dense` for dense recall fused with BM25 (needs pgvector and `pip install -e '.[dense]'`, D-61) |
| `kk status` | who you are, what you can see, what is waiting for review |
| `kk review list \| show \| approve \| reject` | the review lane, as yourself |
| `kk scan --repo DIR` | ingest a repository (ingest filter first; needs gitleaks) |
| `kk token ROLE [--profile memory]` | a bearer token for MCP over HTTP; `--profile memory` shows the four memory tools |
| `kk module install DIR` | a module from `modules/`: its own login and a locked-down container |
| `kk export` / `kk import FILE` | every claim with its provenance as N-Quads, scopes as named graphs |

Open http://127.0.0.1:8000/ui/ and sign in as `owner` with the password `kk person add` printed (or
paste a token, `kk token person_owner`): search and explain,
the review queue, change sets, the graph, and view modules (`modules/view-modules` is the pattern).

Or as a compose stack (Postgres, then one image running `kk serve` behind Caddy at
http://127.0.0.1:8080/mcp and http://127.0.0.1:8080/ui/):

```sh
cp deploy/.env.example deploy/.env       # fill in passwords and KK_JWT_SECRET (>= 32 chars), never commit it
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm init kk agent add claude-code
```

In an agent harness: `docs/harness.md`. To check the kernel end to end in a throwaway database (no
Docker): `sh scripts/smoke_kernel.sh`.

## Layout

```
kk/        the kk command and the module library (kk.lib)
kernel/    schema (frozen v0), JSON Schemas for change sets and manifests, Python contracts
ontology/  kk-core (the module contract), profile packs, packs.yaml, slots.yaml (42 slots)
shapes/    SHACL shapes and fixtures
modules/   default modules: manifest, migration, code, tests each
deploy/    compose stack, Postgres image, Caddyfile, MCP client config
scripts/   kernel smoke test, manifest checks
parked/    modules and scripts outside the kernel (runners, decoders, UI); not maintained
eval/      eval sets
docs/      spec, milestone, decisions, MCP servers, sources, spikes
```

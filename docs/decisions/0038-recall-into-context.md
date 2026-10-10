# 0038. Recall into context: a tool, resources and a hook

Date: 2026-10-07 · Status: proposed (no code until reviewed, as CLAUDE.md asks for recall;
the owner moved it ahead of ADR 0035's order on 2026-10-07)

## Context

Agents read the kernel only through tools they must know how to drive:
- `lookup_entities` for names;
- `query_graph` for Cypher;
- `query_log` for entries;
- `get_schema_slice` for the ontology.

So each agent assembles its own context, by hand, every time. Three things follow:
- **Unread facts.** An agent that writes no Cypher never sees what the kernel knows.
- **No priorities.** Nothing ranks what matters for a task within what a context window holds.
- **No record of what was shown.** `cite` records what an answer relied on. Nothing records
  what an agent was shown before it acted, so nobody can check whether it saw the
  disagreement it went on to ignore.

The earlier ADRs left recall here:
- ADR 0026 put recall in scope as "the read side of context engineering", a proposed ADR
  first.
- ADR 0035 deferred it until an engagement needs it.
- ADR 0037 made it stage 1 on the road to a cognitive model, with a gate: on held-out
  questions, answers with recall beat answers without it, and every sentence cites.

The owner has now moved it forward, and asked for two ways in:
- **callable MCP tools**, for an agent that decides when to look;
- **a context hook for agent loops**, so that the facts arrive before the model's turn
  without the agent asking.

**GOAT** (the Graph Of All Things) needs recall too. GOAT is a swarm of workers that
monitors many sources and builds one large world model. A model that large is useful only
if an agent can get the right few hundred lines of it into a context window.

**What the harnesses offer** (Claude Code's hooks reference, read 2026-10-07):
- **Two events add text before the model's turn:**
  - `UserPromptSubmit`, which receives the prompt;
  - `SessionStart`, on startup, resume, clear, compact or fork.

  The hook prints either plain text or JSON with `hookSpecificOutput.additionalContext`.
  Each field is capped at 10,000 characters; past the cap, the model sees only a file path
  and a preview.
- **The default timeout for `UserPromptSubmit` is 30 seconds.** In the Agent SDK, a callback
  that times out blocks the prompt.
- **The Agent SDK** takes the same hooks in-process; the Python SDK has no `SessionStart`
  callback.
- **A Claude Code plugin** can ship hooks and an MCP server together.
- **Other harnesses** (Codex CLI, Gemini CLI, Cursor) reportedly have similar events. They
  are unverified until their configurations are written.

## Decision (proposed)

### Recall is a decoder that ranks in the database

Recall reads the kernel at one log offset and writes nothing except an optional trace of
what it showed (below). It follows the decoder contract of ADR 0036:
- every fact carries the ids to cite;
- erased words stay erased;
- sources are attributed to collections, never to names.

**No model ranks or summarises** inside the kernel or the gateway. The gateway may embed
the task, as it already embeds names and passages (ADR 0009). A harness may rerank what
recall returns with its own model, outside.

**`kernel.recall(request jsonb) RETURNS jsonb`** is a read function in a new file,
`kernel/sql/91_recall.sql`. For a given request, offset and task vector, it returns the
same pack every time. It works in five steps.

1. **Seeds:** the nodes the task is about, each with a score from 0 to 1 and the reason it
   matched.
   - Nodes named in `focus`: 1.0.
   - Names in the task: windows of one to four words of the normalised task, matched exactly
     against names (1.0) and aliases (0.95), else by trigram similarity at 0.5 or more, using
     the existing indexes.
   - Embedding neighbours, when the gateway sends the task's vector: cosine similarity to
     node embeddings, at or above a threshold.
   - Claim text: full-text search over **unsealed** claims, through a new GIN index
     restricted to claims with no `text_key`. The nodes those claims touched become seeds,
     weighted by rank.
   - **Sealed text is never indexed.** Sealed claims and sealed agents' names are reached
     through the nodes they are about, or through an identity key, and opened only when
     shown. An index of sealed words would itself be personal data that erasure could not
     reach.
2. **Expansion:** the edges at each seed, then one more hop when `hops` is 2. Each node keeps
   at most 25 edges, the best-supported first. Reads use the relational `kernel.edges` table,
   not AGE.
3. **Score,** for each fact (an edge, or a claim node):
   - the seed score of its endpoint, ×1.5 when both endpoints are seeds;
   - ×0.5 on the second hop;
   - by belief: accepted 1.0, **contested 1.1**, unknown 0.6, rejected 0.3. A disagreement
     is the thing an agent must not miss, so it ranks above an agreed fact.
   - by validity at `valid_at` (default now): valid 1.0, ended 0.4, not yet started 0.6.

   Record time never enters the score: no decay, as in belief. Ties are broken by id.
4. **Evidence,** for each fact:
   - the assertions that count for it (`kernel.counted_assertions`);
   - for each side (for and against), up to two quotes with their collection, uri and
     basis, from the claims behind those assertions;
   - source and origin counts on each side.

   Erased claims show no words. Redacted claims are left out.
5. **Budget:** facts are added by score until the rendered size reaches `budget` (characters;
   8,000 by default, at most 9,000 for a hook, under the harness's cap). Then come the open
   questions:
   - the contested facts among the seeds;
   - unresolved claims (text with no ops) that match the task.

**Request:**
```json
{"task": "...", "focus": ["ent_..."], "vector": [0.1, ...], "hops": 1,
 "namespaces": ["process"], "collections": ["interview:..."], "valid_at": "2026-10-07",
 "known_at_offset": 412, "budget": 8000}
```

**Pack:**
```json
{"at_offset": 412, "head_offset": 430, "as_of": {...},
 "seeds": [{"id": "ent_...", "name": "...", "matched": "name"}],
 "facts": [{"id": "edg_...", "from": {...}, "edge": "flows_to", "to": {...}, "props": {...},
            "belief": {"status": "contested", "score": 0.1, "sources_for": 2,
                       "sources_against": 1, "origins_for": 2, "origins_against": 1},
            "valid_from": "...", "valid_to": null, "score": 1.1,
            "evidence": {"for": [{"claim": "clm_...", "quote": "...", "collection": "...",
                                  "uri": "...", "basis": "reported"}], "against": [...]}}],
 "open": {"contested": ["edg_..."], "unresolved": [{"claim": "clm_...", "text": "...",
                                                   "collection": "..."}]},
 "dropped": 12}
```

With `known_at_offset`, the pack is read as the kernel believed then, through
`kernel.state_as_of`, as `query_graph` does.

### Four ways in, one function

1. **The MCP tool `recall`** (a read tool):
   - **Parameters:** `task`, `focus`, `hops`, `namespaces`, `collections`, `valid_at`,
     `known_at_offset`, `budget`, `format` (`markdown` or `json`), `record`.
   - **Returns:** the pack, rendered as Markdown or JSON, with `head_offset` for the next
     write and a `recall_id` when recorded.
   - **The Markdown** groups facts under their seed and puts each fact's id in brackets, to
     cite. A contested fact shows both sides with their collections. It opens with the
     offset it was read at.
   - The tools stay small: query_graph remains for what recall does not answer.
2. **MCP resource templates,** for harnesses that let a person attach a resource:
   - `wmk://recall/node/{id}`: the pack about one node;
   - `wmk://recall/contested`: the open disagreements;
   - `wmk://recall/unresolved`: what is waiting to be placed.
3. **`POST /recall` on the gateway,** a plain JSON route beside `/healthz`.
   - It takes the request above, plus `format` and `record`.
   - It exists because a hook runs before every prompt, and a POST costs less than starting
     an MCP session.
   - It is published where the MCP endpoint is (localhost only) and exposes nothing that
     endpoint does not.
4. **`wmk-recall`, the hook client:** one standard-library Python file in `hooks/`, so that
   any harness can run it with no install.
   - `wmk-recall claude-code` reads the hook's JSON on stdin and prints `additionalContext`:
     - on `UserPromptSubmit`, the task is the prompt;
     - on `SessionStart`, it prints a standing brief: the open questions about the focus
       configured for the project (`WMK_RECALL_FOCUS`, or `.wmk/recall.yaml`).
   - `wmk-recall text "..."` prints Markdown, for any loop that runs a command.
   - **`wmk_recall.context(task, …)`** is the same call as a function, for loops in
     Python: an Agent SDK `UserPromptSubmit` callback, or any custom loop.
   - **The hook fails open.** After two seconds, or on any error, it prints nothing and
     exits 0, so a slow kernel never blocks the person's prompt.
   - **Examples:** a Claude Code settings snippet and a plugin layout (the MCP server, the
     skills and the hook together) ship beside it. Other harnesses get theirs when their
     hook formats are checked.

### What an agent was shown: traces through `cite`

A recorded recall is a cite of a second kind. It keeps the one write path, and no new
function writes data.
- **`kernel.cites` gains a `kind` column:** `answer` (as today) or `shown`.
- **`kernel.cite` takes `"shown": true`.** It stores one row per fact shown. Each row holds
  the fact's id, its edge and claim ids, and the assertions that counted for them at the
  offset read; the sentence column holds only the fact's id.
- **No text is stored:** not the task, not the facts' words. A prompt or a sealed claim's
  words would otherwise be stored unsealed. The OTel trace and span ids link the trace to
  the harness's run.
- **An answer can name its `recall_id`.** Then two shares are queries:
  - of the facts shown, how many were cited;
  - of the facts cited, how many were shown.

  They measure whether recall shows what agents use.
- **The tool records by default.** The hook records only when `WMK_RECALL_RECORD=1`,
  because it runs before every prompt.

### Privacy and telemetry

- **Spans** carry the number of seeds, facts and characters and the offset. They never
  carry the task or the facts' text (invariant 9).
- **Reading:** recall reads through the gateway's reader role, as every read tool does.
  What that role can open, recall can show; erased text reads `[erased]` and shows no words.
- **Scopes are not built:** which reader may see which collection. GOAT's private segments
  need them, and its ADR decides them. Until then, `collections` and `namespaces` are
  filters, not permissions.

### The skill

The core skill's "Read first" step starts with `recall` on the task. It then uses
`lookup_entities` for names it will write, and `query_graph` for what recall did not
answer. "Answer with receipts" cites the bracketed ids and passes the `recall_id`.

### Evals (the gate)

**A recall set, `evals/recall/`, run by `make eval` after the resolution set:**
- **Questions** over the fixtures (Northwind's views and log, the research fixture, the
  self-model). Each lists the facts needed to answer it and, where it applies, the facts
  that must not appear.
- **Measures:**
  - the share of needed facts in the pack within its budget;
  - the share of the pack that was needed;
  - each needed contested fact shown with both sides;
  - no erased words;
  - the same pack twice for the same request and offset.
- **Floors** are set from the first measured run, and CI holds them.

**A live comparison, `make live`, not in CI:**
- the same model answers held-out questions with recall and without it;
- answers are scored against the expected facts, with the share of sentences that cite.

**This is stage 1's gate in ADR 0037.**

**Speed:** a benchmark measures a pack over a generated graph of a million edges, with
250 ms as the 95th-percentile target.

## Consequences

**Kernel 0.7.0:**
- `kernel.recall`;
- the full-text index over unsealed claim text;
- `cites.kind` and `"shown"` in `kernel.cite`.

There is no new write path and no new log operation. Replay copies cites as it does today.

**Gateway:** the `recall` tool, three resource templates, `POST /recall` and the Markdown
rendering, with the span names in `otel.py`.

**New folder:** `hooks/`, holding `wmk-recall`, the Claude Code example and a plugin layout.

**Skills and evals:**
- the core skill's read and answer steps;
- `evals/recall/` with its floors;
- a live comparison.

**CLAUDE.md** records that recall was moved forward and the hook is in scope. The
out-of-scope list is unchanged: recall shows facts and records what it showed. It does not
capture what an agent does (observer runs).

**Risks:**
- **Names are matched on words:** a task that names nothing the kernel knows gets few
  seeds. Embeddings help when an endpoint is configured. The evals report packs with no
  seeds, so the gap is seen.
- **Hook cost:** a pack on every prompt costs one database read and the budget's characters
  of context. `budget` and the 2-second limit bound both.
- **Context poisoning:** a pack is only as good as what was written. Belief, contested sides
  and quotes travel with every fact, so an agent sees how sure the kernel is, not just
  what it holds.
- **Scale beyond one database** (GOAT) needs federation or scopes. That is GOAT's ADR, not
  this one.

**Build order once accepted:**
1. `kernel.recall` with the recall set;
2. the tool and the Markdown;
3. `POST /recall` and the hook;
4. resources and traces;
5. the live comparison.

# Milestone 6 — agent memory without baggage

**Why this, now** (D-85). D-84 positions the kernel as an agent memory layer for developers (beside Mem0,
Zep, Letta) and as a knowledge management system sold as distributions. The developer door opens first,
and the org-maps distribution reuses most of it (extraction, entity resolution, contradictions, briefings).

The kernel's guarantees are what make a memory trustworthy for an agent: who said it, with what quote,
how sure, superseded rather than overwritten, checked by another method. Today an agent pays for them
directly. It builds change sets, picks IRIs and ontology terms, reads raw triples, waits for review to
see its own writes, and carries about 14 tools in its context. That is the same reason human KMS failed:
capture costs too much at the moment of work. The milestone's principle is **rigour behind the
interface**. Keep every invariant, and move the cost off the agent:

- **Write by default without writing:** hooks record sessions, and existing outputs (commits, PRs,
  decision logs) are read as they are. A different method structures them afterwards, so invariant 5
  holds by construction. When an agent does write, it is one sentence.
- **Read as a briefing:** cited prose within a token budget, attributed (the owner decided / the agent
  proposed), with handles to expand. It also says what changed since the agent last looked.
- **A small door:** a memory profile of four tools. Everything else is for operators and modules.

Owner, Oct 2026, asking whether an autonomous agent would want this or find it baggage: "there are more
solutions we could figure out to the issues you mentioned". The answers are below, one task each.

Rules carry over: standard vocabularies first (D-40), permissive licences (D-11), modules under their
own roles, people decide, models propose. Two tasks need kernel changes. Each is marked; neither starts
until the owner confirms it.

## The issues, and what answers them

| Issue for an agent | Answer | Task |
| --- | --- | --- |
| Writing in the kernel's format mid-task (IRIs, terms, quotes) | passive capture; existing outputs read as they are; `remember(kind, text)`; inline `Decision:` / `Remember:` lines | T4, T6, T1 |
| Reading raw triples costs tokens | a cited briefing with handles; "what changed since you last looked"; recall injected per prompt within a budget | T7, T3 |
| Its own writes are invisible until reviewed | a private working-memory scope, applied at once at lower belief, promoted through review; pending writes shown to their author, marked | T2 |
| Too many tools | MCP profiles: `memory` (four tools) and `operator` (all); briefings as MCP resources | T1 |
| Choosing ontology terms | the extractor maps terms, never the agent; `find_term` and `propose_term`; KK030 answered with suggestions; provisional terms (owner decision) | T5 |
| Trusting its own summaries after compaction | the compaction verifier: the summary checked line by line against the recorded transcript | T8 |
| Acting on a false memory ("the owner approved…") | attribution in every briefing line; later, decision gates that require an owner-attributed claim | T7 |
| Several agents at once | subagents get the briefing; their findings return as claims attributed to them | T1, T7 |

## Tasks

| ID | Task | Check | Status |
| --- | --- | --- | --- |
| M6-T0 | **Reactivity, amended D-83** (D-86; kernel change, owner to confirm). In the kernel, `kernel.current_edge`: one row per current claim with an entity object, kept in step by triggers in the apply transaction, so redaction reaches it at once. Plus an applied sequence on change sets, a cursor. Outside the kernel, a **detector module** reads applied change sets in order and runs each detector on its owner's connection (clearance applies). It compares against stored matches and records formed/dissolved events. Detectors are SQL, or openCypher once a graph projection exists (AGE, or SQL/PGQ over `current_edge`). `map-gaps` moves over first | kernel tests: the edge table follows supersede, retraction, redaction and a failed apply; concurrent applies; the detector module's events match the spike's scenario (`docs/spikes/D83-age.md`); map-gaps' tests pass as a reactor | |
| M6-T1 | **The agent's door: MCP profiles**. `memory`: `remember(kind, text, about?)`, `recall(question, budget?)`, `explain(handle)`, `changes_since()`. `operator`: today's tools. The profile is chosen per login. Briefings are also MCP resources. Inline `Decision:` / `Remember:` lines in an agent's replies are picked up by the interaction encoder | an agent with the `memory` profile sees four tools; each answer cites claim handles and shows belief; an inline line becomes a claim quoting it | **done** (D-88): `scripts/e2e_kernel.py` and `e2e_up.py` (four tools over stdio and HTTP, remember, recall, explain a handle, a correction supersedes, changes since, the briefing as a resource), `modules/kernel-mcp/tests/test_briefing.py`, `modules/encoder-interaction/tests/test_encode.py` (inline lines) |
| M6-T2 | **Working memory**: a private scope per agent (optionally per session) whose change sets apply at once at lower belief. `promote` sends claims to shared memory through review. `recall` includes the author's own pending writes, marked unconfirmed | an agent recalls what it wrote a moment ago; another agent does not see it until it is promoted and approved | |
| M6-T3 | **"What changed since you last looked"**: a cursor per agent over applied change sets (needs T0), summarised as decisions superseded, questions answered, claims contradicted, new sources | after another agent's work, `changes_since` lists exactly those changes, cited, and the cursor moves | |
| M6-T4 | **Memory extractor** (a processor using a small model, or a skill): from recorded sessions and messages, facts, preferences, decisions with rationale, commitments and corrections, each quoting its message. A change supersedes the old claim instead of overwriting it. Terms are chosen inside the extractor (T5). The interviewer becomes a profile of it: the agent just interviews, the extractor builds the maps (this absorbs M5-T6's eval) | fixture sessions give the expected claims; the quote verifier supports every quote; a correction supersedes; the scripted interview still yields M5-T4's maps | |
| M6-T5 | **Vocabulary access**. `find_term(text)` searches installed packs (labels, definitions), then the catalogs' search APIs (LOV, schema.org, EBI OLS). `propose_term` puts a standard term or pack subset into the review lane. KK030 reports carry the nearest standard terms. A schema.org subset pack for everyday memory (people, places, events, products), mapped with SSSOM where it overlaps ORG, PROV and FOAF. A vocabulary view in the UI. *Provisional terms* (unknown concepts get a provisional IRI and wait in review instead of failing with KK030) are a kernel change: owner decision (O-8) | `find_term` ranks the right term first on a fixture list; an approved proposal makes the term usable without a migration; the view lists packs, ownership, usage and pending proposals | |
| M6-T6 | **Git and PR encoder**: commits, pull requests and review threads (and `decisions.md`-style decision logs) as claims. Decisions keep their rationale; each claim is referenced by commit or PR and quote | this repository's history gives its decisions with rationale; re-running changes nothing | |
| M6-T7 | **Context pack decoder**: a briefing for the current task within a token budget. Lines carry who said them (owner, person, this agent, another agent) and belief, and short handles that expand on request; IRIs are shortened with a legend. Delivered at session start (hooks), per prompt (only above a relevance threshold) and as an MCP resource. An `AGENTS.md` / `CLAUDE.md` decoder writes the same for agents without MCP | on fixture memories, the briefing stays within budget, attributes every line correctly and cites every claim; an owner decision and an agent proposal are never confused | |
| M6-T8 | **Compaction verifier**: the harness's pre-compaction hook records the full transcript (the interaction encoder); after compaction the summary is checked line by line against it with the quote verifier's method: supported, unsupported, or said by the agent and not by the person. Look up the current hook events at build time | on a fixture session, an injected false line "the owner approved X" is flagged; true lines are supported | |
| M6-T9 | **Entity resolution and contradictions** (processors). Resolution: names, roles and mentions of the same thing linked, `skos:exactMatch` when certain and `closeMatch` for review (Splink, MIT, plus embeddings). Contradictions: claims that cannot both hold (functional properties from the packs) become annotations, lower belief and questions, as map-gaps does | fixture people and organisations resolve as expected; an injected contradiction becomes a question and nothing is deleted | |
| M6-T10 | **Evals**: LongMemEval (check its licence first; data fetched, not committed), answers cited, scores as DQV claims next to published baselines. Tokens per task for an agent with and without the kernel on scripted multi-session tasks. A fixture agent session (written for the eval; real session transcripts are never committed) replayed through compaction: briefing against summary | scores recorded; the token comparison and the replay reported as they come out | |
| M6-T11 | **Conclusions with premises:** `remember(kind="conclusion", text, because=[handles])` writes an inferred claim `derived_from` the cited claims, in the narrowest of their scopes (the kernel refuses a wider one); `explain` shows the premises; briefings mark conclusions and their premise count | a conclusion cites its premises; a conclusion from a secret premise is secret; a conclusion without premises is refused | **done** (D-90): `scripts/e2e_kernel.py` | |
| M6-T12 | **Truth maintenance** (a processor): when a claim is deprecated, every claim derived from it, transitively, gets a verification `inconclusive` (method: truth maintenance, note: which premise fell). The belief model caps belief for a claim with a fallen premise. The author's `changes_since` lists it | supersede a premise: the conclusion's belief drops and its author is told; restore the premise: belief returns; nothing is deleted | **done** (D-90): `scripts/e2e_kernel.py` (supersede a premise: flagged, belief 0.25, kept), `modules/truth-maintenance/tests/test_plan.py` (flag, restore, flag again), belief eval case `withdrawn-premise-is-capped` | |
| M6-T13 | **Recall v2:** `as_of` (valid and recorded time); conflicting current claims returned together; one hop of graph neighbours seeded by the top hits; evidence type and verification as ranking features; the quote as a second text. Each switched on only if it helps on the evals | LongMemEval knowledge-update and temporal against recall v1; FactConsolidation (licence to check); the D-60 recall checks still pass | started (D-90): hybrid BM25 normalised by its best score; memory recall leaves out the vocabulary | |
| M6-T14 | **Rules from the packs** (a processor, pySHACL's SHACL rules, already a dependency): inverse, transitive and sub-class conclusions as inferred claims with premises; a functional property with two values becomes a contradiction for M6-T9 | a reports-to chain gives its transitive conclusions; retracting a link withdraws them (through M6-T12) | |
| M6-T15 | **Why believed:** `explain` gains a support and attack summary (derivations, verifications, contradictions, supersedes), and the belief model says which of them moved the score | on a fixture, the explanation names each supporting and attacking claim | |

## Gates

| Gate | Check | Status |
| --- | --- | --- |
| M6-G1 | **Memory without baggage**: on a scripted multi-session task, an agent with only the `memory` profile recalls earlier decisions with citations and attribution. It wrote nothing beyond one-line `remember` calls and inline lines. Its tokens per task are reported against the same agent without the kernel | |
| M6-G2 | **A public memory benchmark**: LongMemEval scored and recorded as claims, every answer citing its claims | |
| M6-G3 | **Summaries checked**: after a compaction, the verifier flags an injected false approval and supports the true lines | |
| M6-G4 | Every new module installs with `kk module install` under the default policy; the kernel tests and all earlier gates still pass | |

## Order

1. **T1 and T7.** They need no kernel change, and they give the briefing that later tasks are judged by.
2. **T10's LongMemEval baseline,** early: a public score is what developers compare.
3. **T4 with T5:** extraction chooses the terms.
4. **T0, T2 and T3.** D-86 is confirmed by D-87.
5. **T6, T8 and T9,** with T10 measuring each as it lands.

T11–T15 (reasoning over claims, `docs/reasoning.md`, D-89) go right after T1: T11 and T12 first,
T13 against the LongMemEval baseline, T14 and T15 with T9.

After this milestone come distributions (M5-T9) and the interview demo (M5-T5) as the first one. Paid
pilots of the process loop (`docs/offer.md`) run alongside, on what works today.

## Carried over from milestone 5

- M5-T5 (the interview demo) and M5-T9 (distributions) follow this milestone.
- M5-T6 (the interview eval) runs inside M6-T4.
- M5-T7 and M5-T8 (code map and localization) are deferred: they serve the developer door, but after
  memory itself.

## Using it on itself

When T1 and T7 land, sessions working on this repository use the kernel as their own memory. The self
encoder, the git and PR encoder and the session hooks capture the work, and each session starts from a
briefing instead of a compaction summary. Friction found there becomes a task here. Real session
transcripts stay local and are never committed.

## Later (proposed in the same review, not yet tasks)

- **Processors:**
  - temporal: valid time from phrases such as "since March"; belief fading for facts not reconfirmed;
    nothing deleted;
  - consolidation: entity profiles and topic digests as inferred claims derived from their sources;
  - salience: what is worth recalling;
  - decision gates: actions that require an owner-attributed decision claim;
  - a shared memory board for several agents: who works on what, as claims.
- **Encoders:**
  - documents (Docling, MIT), keeping sections and quotes;
  - structured data through the standard mapping language (RML, with morph-kgc, Apache-2.0) instead of
    one encoder per format;
  - work trackers (GitHub issues and PRs, then Linear or Jira through connectors);
  - saved web pages.
- **Decoders:**
  - a wiki export, one cited Markdown page per entity, with edits returning as change sets;
  - a digest for decision makers: what changed, what was decided, what is contradicted.
- **Evals:** LoCoMo beside LongMemEval (licence to check).

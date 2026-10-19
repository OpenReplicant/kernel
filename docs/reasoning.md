# Reasoning over claims: what this architecture makes possible (Oct 2026)

The owner asked for "something that can reason about anything", with more thought given to the kinds of
thinking this design allows and to retrieval. This note covers what the kernel adds to a model's own
reasoning, the strongest objections, what survives them, and the tasks that follow (D-89). M6-T11
and M6-T12 are built (D-90).

## 1. What a reasoner gets here that a vector store does not give it

A vector store returns passages. The kernel returns **claims**. Each claim carries:

- who said it, in which run;
- its source and the quote it rests on;
- how sure the kernel is (belief);
- when it holds (valid time) and when the kernel learned it (recorded time);
- what it was derived from, and what derives from it;
- whether another method checked it;
- who may read it.

Nothing is overwritten. Supersede, refute and retract are history, so the kernel can answer what was
believed at any moment and why it changed.

These are the raw materials of reasoning that can be checked. A model does the thinking; the kernel makes
that thinking **durable** (the next agent starts from it), **checkable** (every step cites its premises,
and invariant 5 means another method verifies it) and **revisable** (when a premise falls, the
conclusions built on it can be found).

## 2. Kinds of thinking, as module patterns

Every pattern below uses the same shape:

1. A step proposes **inferred** claims, `derived_from` their premises, at low belief, recorded as a run.
2. A different method verifies them.
3. The belief model weighs them.
4. A person reviews them when it matters.

Invariant 3 already keeps a conclusion's scope at least as narrow as its premises'.

| Thinking | What it is here | Exists | Missing |
| --- | --- | --- | --- |
| **Recall** | find the claims bearing on a question | BM25, dense, re-ranking (D-60, D-61, D-66); briefings (D-88) | time, contradictions and graph neighbours in ranking (§3) |
| **Deduction** | conclusions that follow from rules the packs define: inverses (`org:headOf` / `org:heads`), transitivity (reports-to chains), sub-classes, functional properties | the pack terms carry `owl:inverseOf` | a rules processor writing inferred claims with their premises |
| **Truth maintenance** | when a premise is superseded, refuted or retracted, everything derived from it is flagged and its belief drops | `derived_from` is recorded | nothing follows it: today a conclusion keeps its belief after its premise falls. **This is the main gap.** |
| **Belief revision** | a new claim supersedes an old one; a contradiction becomes a question | supersede, refute, `replaces` (D-88) | contradiction detection (M6-T9) |
| **Abduction** | the best explanation for an observation (an anomaly's likely cause; why a process deviates) | anomaly and adaptation planning (M2) propose causes | hypotheses as first-class inferred claims, tested and kept or refuted |
| **Induction** | a general rule from instances (the process as it really runs) | learner-process, conformance (M2, M4) | evidence counts on generalisations; their belief from support, not a fixed value |
| **Argumentation** | why the kernel believes something: what supports it, what attacks it | verifications, derivations, status history (`explain`) | one view of support and attack per claim, and belief explained from it |
| **Temporal** | what was true then; what we believed then; what changed since | valid and recorded time on every claim; `changes_since` (D-88) | `as_of` in recall |
| **Metacognition** | what the system knows about itself and does not know | self-model (modules, slots); map-gaps turns gaps into questions (M5) | open questions as claims an agent can pick up, and answers that close them |
| **Prospective memory** | remembering to remember: a memory that must come back in a situation (a name, a date, a path, an action), whatever its similarity to the question | recall cues (`kk:recallWhen`, D-91, M6-T16) | cues proposed by the extractor (M6-T4) |
| **Planning** | a plan as steps, checked before it runs | P-Plan, MAPE-K, approval before an actuator acts (M2) | — |

## 3. Retrieval methods

What wins memory benchmarks today (`docs/recall-research.md`):

- hybrid lexical and dense retrieval;
- neighbouring context;
- extracted facts given alongside the raw quotes;
- time-aware filtering;
- a good reader model.

No graph database wins on its own. Where everyone fails is **knowledge update and contradiction**:

- FactConsolidation multi-hop: 2–5% for every system, long context included.
- BEAM contradiction resolution: at most 0.05 at 1M tokens (`docs/benchmarks.md`).

That is the ground this design was built for. The methods, in the order to add them:

1. **Latest wins, history on request.** Superseded claims leave the ranking but stay findable. Recall
   already filters deprecated claims; briefings should say "replaced on <date>, was: …" when asked about
   something that changed.
2. **Contradiction-aware.** When two current claims cannot both hold, recall returns them together,
   never one side silently. Needs M6-T9's detector.
3. **Time-aware.** `recall(question, as_of)` filters on valid time and recorded time. Phrases such as
   "last week" set the window.
4. **Two granularities.** Rank the claim and also the quote it rests on: the claim's words are short,
   while its quote carries context.
5. **Graph neighbours.** Seeded by the top hits, add one or two hops over current entity claims. Use a
   recursive CTE now, and `current_edge` once M6-T0 lands. Personalised PageRank (HippoRAG 2) only if it
   reproduces its multi-hop gains here.
6. **Provenance-aware ranking.** Use the evidence order the spec already defines (observed >
   deterministic > extracted > inferred; testimony weighed by the person), plus verification, as a
   ranking feature, not only as belief.
7. **Structured questions.** "How many", "which ones" and "since when" are queries, not passages. A
   read-only SQL path over the `api` views, under the caller's role, answers them exactly.
8. **Iterative recall.** For a hard question: break it into parts, recall each, read, ask again. The
   trace is a run, and the answer is an inferred claim built from what it cites (§5).

## 4. The strongest objections

- **Models already reason.** Formal rules are brittle and need clean ontologies that real data lacks.
- **Every inference adds claims:** noise, storage and review load. Truth maintenance costs work on
  every status change.
- **Benchmarks reward retrieval and a good reader.** Structure-heavy methods (GraphRAG, RAPTOR,
  LightRAG) scored below plain dense retrieval on average.
- **"Reason about anything" is scope creep.** The paid work is process pilots and the MAPE-K loop.

## 5. Synthesis: what survives

- **The model thinks; the kernel keeps the thinking honest.** No in-house reasoner and no theorem
  prover. Rules are limited to the semantics the packs already declare: inverse, transitive, sub-class,
  functional. They are deterministic and cheap, and the agent can trust them.
- **Conclusions are claims.** When an agent concludes something from what it recalled, it records the
  conclusion with its premises:

  ```
  remember(kind="conclusion", text, because=[#handles])
  ```

  The result is an inferred claim, `derived_from` those claims. This needs no new tool. It goes in
  the narrowest of its premises' scopes (invariant 3), and invariant 5 means a different method checks it.
- **Truth maintenance is the one new mechanism worth building now.** It is small: a processor that
  follows `derived_from` when a premise is deprecated. The belief model then counts a fallen premise.
  The conclusion's author sees it in `changes_since`. No vector store does this, and it is what the
  knowledge-update benchmarks measure.
- **Retrieval stays hybrid and plain.** Add time, contradictions and neighbours, and keep each only if a
  benchmark says so: LongMemEval's knowledge-update and temporal categories, FactConsolidation, and a
  MuSiQue subset for multi-hop.
- **It serves the paid work too.** Abduction and truth maintenance are the Analyse and Knowledge steps
  of MAPE-K. When a deviation's assumed cause is refuted, the plans built on it are flagged before an
  actuator runs them.

## 6. Tasks (added to milestone 6, D-89)

| ID | Task | Check |
| --- | --- | --- |
| M6-T11 | **Conclusions with premises:** `remember(kind="conclusion", text, because=[handles])` writes an inferred claim `derived_from` the cited claims, in the narrowest of their scopes (the kernel refuses a wider one); `explain` shows the premises; briefings mark conclusions and their premise count | a conclusion cites its premises; a conclusion from a secret premise is secret; a conclusion without premises is refused |
| M6-T12 | **Truth maintenance** (a processor): when a claim is deprecated, every claim derived from it, transitively, gets a verification `inconclusive` (method: truth maintenance, note: which premise fell). The belief model caps belief for a claim with a fallen premise. The author's `changes_since` lists it | supersede a premise: the conclusion's belief drops and its author is told; restore the premise: belief returns; nothing is deleted |
| M6-T13 | **Recall v2:** `as_of` (valid and recorded time); conflicting current claims returned together; one hop of graph neighbours seeded by the top hits; evidence type and verification as ranking features; the quote as a second text. Each switched on only if it helps on the evals | LongMemEval knowledge-update and temporal against recall v1; FactConsolidation (licence to check); the D-60 recall checks still pass |
| M6-T14 | **Rules from the packs** (a processor, pySHACL's SHACL rules, already a dependency): inverse, transitive and sub-class conclusions as inferred claims with premises; a functional property with two values becomes a contradiction for M6-T9 | a reports-to chain gives its transitive conclusions; retracting a link withdraws them (through M6-T12) |
| M6-T15 | **Why believed:** `explain` gains a support and attack summary (derivations, verifications, contradictions, supersedes), and the belief model says which of them moved the score | on a fixture, the explanation names each supporting and attacking claim |

Order: T11 and T12 first: they are small and give the kernel something no memory layer has. Then T13
measured against the LongMemEval baseline (M6-T10). T14 and T15 come with M6-T9.

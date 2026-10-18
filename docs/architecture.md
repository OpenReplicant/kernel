# How it works: a symbolic store that learns, with models at its edges

The knowledge kernel is a **neurosymbolic** system. What it knows is held as symbols: explicit
statements in standard vocabularies, in one Postgres database. Neural models work at the edges. Language
models turn messy input into proposed statements and answer from statements. Embedding models make the
statements easy to find. The store grows and corrects itself as it is used, and every step can be
traced, questioned and reversed.

```
   documents, logs, chats,        ┌──────────────── the kernel (Postgres) ────────────────┐
   agent sessions, code,  ─────▶  │  claims: subject · predicate · object · qualifiers     │ ─────▶  answers that cite
   sensors, interviews   encoders │  + who said it · from what · belief · scope · status   │  recall  claims and belief
            ▲            (LLMs at │                                                        │  (BM25 + embeddings)
            │             the edge│  change sets ─▶ validated ─▶ reviewed ─▶ applied       │
            │             propose)└────────────────────────▲───────────────┬─────────────┘
            │                                              │               │ a pattern forms
            │                             people approve   │               ▼
            └──── actuators act on the world ◀── plans ◀── review lane ◀── processors: conformance,
                  (only what was approved)                                 anomaly, belief, verifier,
                                                                           learner (propose, never decide)
```

## The representation is symbolic

A neural network keeps what it has learned in a latent space: weights and vectors that work but cannot
be read. The kernel keeps it in **claims**. Each claim is one statement, such as *"Send invoice is
followed by Ship goods"*, *"Alice holds the post of buyer"* or *"the wait reached 122 h"*. Each one
carries:

- **who made it and when** (an agent and a recorded run);
- **what it came from** (a stored file and the exact quote, an observation window, or the claims it
  was derived from);
- **its belief**, a number between 0 and 1 that support and contradiction move;
- **its scope**, which says who may see it;
- **its status**: current, superseded, refuted or retracted. Nothing is deleted.

Claims use standard vocabularies wherever one exists: BPMN terms for processes, SOSA for observations,
PROV for provenance, Web Annotation for findings, and (from milestone 5) W3C ORG for organisations. So
what the kernel learns stays usable by other tools and readable by people.

## The neural parts sit at the edges

- **Language models** extract claims from unstructured input (a written procedure, an interview, a
  paper), answer questions from recalled claims, and propose: causes, plans, revisions. What they
  propose is a change set like any other. It is validated, often reviewed by a person, and never
  verified by the model that made it.
- **Embedding models** build a vector index over the claims for recall, fused with keyword search
  (BM25). That vector space is derived from the claims and can be rebuilt from them at any time. It is
  not where knowledge lives.
- **Belief** is the soft, numeric layer between the two: a model's statement starts uncertain and
  gains or loses belief as independent sources support or contradict it, or as a verifier checks its
  quote.

## How it learns

Learning is the store filling and correcting itself, through the same governed path every time:

1. **Encoders** turn sources into claims. Re-reading a changed source supersedes what it said before.
2. **Processors** wake when a pattern forms, for example when a new observed path or a confirmed
   discrepancy appears. They then compare, verify, re-score belief, or find what is missing.
3. **Learners** turn what keeps happening into proposals. Example: "practice takes this path in 8% of
   cases; make it part of the documented process?"
4. **People decide** in the review lane. Once something is approved, the next pass reflects it: the
   discrepancy is retracted because practice and documentation now agree.

The more the store holds, the more each new input can be compared against, the more precisely recall
finds the right claims, and the more grounded the models' answers are.

| | A neural model learning online | The knowledge kernel |
| --- | --- | --- |
| Where knowledge lives | weights and latent vectors | claims with provenance and belief |
| Learning step | gradient update | a change set: add, supersede, re-score, approve |
| Forgetting | catastrophic forgetting is a risk | nothing is deleted; every earlier state can be explained |
| Generalisation | implicit | explicit: processors and learners propose rules, people approve them |
| Correcting a mistake | retrain, or hope | refute, supersede or redact one claim; its dependants are traceable |
| Explaining an answer | hard | every answer cites claim ids and shows their belief |

## What keeps it honest

Six invariants are enforced by the database itself, not by a model's judgment:

1. Every claim is attributed and referenced, and an extracted claim carries its quote.
2. Nothing is deleted; redaction removes content but keeps identity and lineage.
3. A claim is never visible more widely than what it was made from.
4. Clearance is granted by people and enforced by Postgres.
5. No method verifies its own output.
6. Every answer cites claim ids with their belief.

## Read next

- `docs/developer-guide.md`: the moving parts, the interfaces, and where to plug in.
- `docs/spec.md`: the specification.
- `docs/modules.md`: what each module takes in and gives out.
- `docs/demo.md`: the process demo.
- `docs/milestone-5.md`: what is being built now.

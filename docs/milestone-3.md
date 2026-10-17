# Milestone 3 — your own data, cited, in minutes

**Why this, now** (D-56). Milestones 1 and 2 proved the kernel and two target uses on synthetic demos.
What stands between that and a product is adoption: connecting a kernel to a harness takes a dozen
commands and a guide, and the third target use (coding agents and their codebase) still encodes only
this repository. Coding agents are also where the most people already work every day. So milestone 3
measures one thing: **how fast a new person gets cited answers about their own work**, and whether
those answers are better than without the kernel.

Rules carry over: kernel v0 stays frozen; every new module runs sandboxed under the operator's policy
(D-55); standard vocabularies first (D-40); permissive licenses (D-11).

## Tasks

| ID | Task | Check | Status |
| --- | --- | --- | --- |
| M3-T0 | **Sandbox policy** (owner, Oct 2026): what a module may reach and use is the operator's grant in `deploy/policy.yaml`, not the manifest's request | an ungranted request is refused; a grant can give more or less; the demos still pass | done (D-55) |
| M3-T1 | **`kk up PROJECT`**: one command connects a project folder to a kernel: starts the compose stack (or reuses a running one), creates the owner and an agent for the project, writes the MCP entry, the skill and the hooks into the project (merging, never overwriting the person's own settings), and prints the UI address and the owner's token | from a fresh checkout, a scripted harness session in a fixture project submits and cites a claim; running it again changes nothing | done (D-57; `scripts/e2e_up.py`) |
| M3-T2 | **Repository encoder** (after M3-T3, D-59) (use 3, generalising the self-encoder): any repository → its packages and their dependencies (pyproject, requirements, package.json, go.mod, Cargo.toml), its documents' headings and its layout, in a standard vocabulary chosen after a catalog check (candidates: CodeMeta, schema.org `SoftwareSourceCode`, SPDX 3); ingest filter first; supersede on change; the session-start hook re-encodes when the commit moved | fixture repositories give the expected claims; a changed manifest supersedes | |
| M3-T3 | **Recall eval, starting with SciFact** (D-58): the abstracts encoded as artifacts and claims; (1) retrieval through the kernel's recall, scored as BEIR does (nDCG@10 against BM25's 0.665), no model needed; (2) verification with a model, with and without the kernel, scored with SciFact's metrics (label and rationale sentences); scores recorded as DQV measurements (spec §12). Data downloaded at eval time (CC BY-NC), never committed | the scorer fails a broken answer (mutation check); stage 1 scored; stage 2 when a model key is configured | stages 1–2 run (D-59; `eval/scifact`); recall is the gap (D-60) |
| M3-T4 | **A public event log at scale** (BPI Challenge, CC BY 4.0, if the license checks out): the event-log encoder and conformance run on it in bounded memory | encodes and checks within a time budget recorded in the decision | |
| M3-T5 | **Recall that beats the model alone** (D-60, `docs/recall-research.md`): BM25 index as a default module (done); then dense embeddings fused with BM25, then a cross-encoder reranker, each measured on SciFact; then agent memory (LongMemEval) | each step recorded in `eval/scifact/runs/`; kept only if it gains | BM25 done (D-60): nDCG@10 0.659, claim accuracy with the kernel 0.91 vs 0.78 alone; dense + BM25 done, opt-in (D-61): nDCG@10 0.723 |
| M3-T6 | **Quote verifier** (a verifier module): an extracted claim's quote must occur in its source artifact; claims whose quotes do not are marked, so belief and review can see it | a fabricated quote is caught; a verbatim one passes | |
| M3-T7 | **Belief from support and contradiction** (belief model v1, competing with v0 in the slot): `cito:supports` and `cito:refutes` from independent sources move a proposition's belief | SciFact stage 3: belief separates supported from contradicted claims | |

## Gates

| Gate | Check | Status |
| --- | --- | --- |
| M3-G1 | **Zero to a cited answer on your own repository**: clone, `kk up <project>`, open the harness: at most three commands and ten minutes, and the agent's first answer about the project cites claims with belief (scripted in CI) | |
| M3-G2 | **Evidence the kernel helps**: on the recall eval, answers with the kernel cite valid claims and score at least as well as without it; the scores are claims | |
| M3-G3 | Every new module installs with `kk module install` under the default policy; the kernel tests and all earlier gates still pass | |

## Later (not this milestone)

People's sign-in through an identity provider (O-6) and hosting beyond local Docker (O-3), both needed
before a shared team instance; the Learn phase of MAPE-K (plans that keep working become rules); a
BPMN process runner; The GOAT.

# Milestone 4 — a demo you can sell

**Why this, now** (D-73). Milestones 1–3 built the kernel, two target uses and the path from a fresh
checkout to cited answers. What a paying customer needs to see is one of those uses working end to end
on data like theirs, behind a sign-in, and getting better as people use it. Process mining with a
learning loop is the most concrete: a public event log at scale (D-72), discrepancies a person can
actually review, and a documented process that learns from what they decide.

Rules carry over: kernel v0 stays frozen; modules run sandboxed under the operator's policy (D-55);
standard vocabularies first (D-40); permissive licenses (D-11); people decide, models propose (invariants
4 and 5).

## Tasks

| ID | Task | Check | Status |
| --- | --- | --- | --- |
| M4-T1 | **Sign-in without an identity provider** (O-6, owner: "postgres users and a caddy configuration"): a person signs in to the UI with their own Postgres login (`kk person add`); Postgres checks the password; Caddy serves HTTPS on a public host name | a wrong password is refused, the right one signs in (UI, headless); the public overlay's Caddyfile adapts to HTTPS on the named host | done (D-74) |
| M4-T2 | **Discrepancies a person can review**: conformance reports what matters, not every rare variant (BPI 2019 gave 495 review items) — a minimum share of cases per discrepancy, the rest summed up in one item per process | BPI 2019: at most 50 review items, covering the largest deviations; the order-to-cash demo unchanged | done (D-75): BPI 2019 gives 35 items instead of 495 (34 paths in at least 1% of cases, and one for the 461 rarer paths); order-to-cash unchanged |
| M4-T3 | **The process learns** (the Learn phase of MAPE-K, for processes): a learner module proposes revisions of the documented model from what keeps happening (a deviation in a large share of cases, or one reviewers keep confirming), each a change set in the review lane derived from its evidence; once a person approves it, the next conformance pass stops flagging it, and the learned model downloads as BPMN | order-to-cash: a frequent deviation becomes a proposal, approved it updates the model and its discrepancy is retracted; the BPMN export opens in a BPMN editor (bpmn-js) | done (D-76): the learner proposes confirmed paths taken in at least 5% of cases; approved in the process view, conformance retracts the discrepancy; the BPMN download parses with bpmn-moddle |
| M4-T4 | **The demo**: one command loads a customer-ready demo (BPI 2019, or the bundled order-to-cash log offline) into a fresh stack, and `docs/demo.md` walks through it: sign in, the process map, the largest deviations with their evidence, approve a learned revision, ask the agent a cited question about the process | scripted in CI on the bundled log, screenshots of each step | |

## Gates

| Gate | Check | Status |
| --- | --- | --- |
| M4-G1 | **The loop, end to end, as a customer sees it**: from a fresh checkout, the demo command; a person signs in, reviews the top deviations, approves a learned revision, and the next conformance pass reflects it; the agent's answer about the process cites claims with belief | |
| M4-G2 | Every new module installs with `kk module install` under the default policy; the kernel tests and all earlier gates still pass | |

## Later (not this milestone)

A BPMN process runner; the Learn phase for operations (plans that keep working become rules); hosting
beyond one machine (O-3); the benchmarks deferred from milestone 3 (M3-T3, T5, T8), run with local models.

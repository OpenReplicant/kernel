# Backlog

Ideas parked until a milestone needs them.

- Modules and chains described in the graph, so an entity can describe and rearrange itself
  (the second iteration's component-graph compiler is a starting point).
- Agent-design papers as modules: decompose a paper into a system, run it, check its
  mechanism, record results as evidence (the second iteration's first application).
- A harder coding benchmark than HumanEval for model comparisons (ClassEval and DS-1000 are
  reachable from cloud sessions; LiveCodeBench needs huggingface.co).
- An OpenAI-compatible router (e.g. LiteLLM, OpenRouter) for many providers behind one API.
- Vocabulary-constrained extraction and entity disambiguation for perceivers.
- Parallel branches and nested loops in compiled chains.
- Outbox bridge as a Go endpoint compiled into the RuleGo server, instead of a Python process
  (S0 used `spike/bridge.py`).
- AgensGraph v2.18.6 (rc1 on Docker Hub at S0) once released.
- Predicates checked against installed ontologies on write (S1 records each module's relations
  on its module vertex but doesn't validate claims against them).
- Write permissions by database role (modules' chains connect as a role without DDL rights),
  instead of trusting writers to set causation and depth honestly.
- Per-element ordering and a dead-letter view for the outbox; outbox pruning or archiving.
- Activation filters beyond equality (`where`), and activations on inherited labels (`thing`).

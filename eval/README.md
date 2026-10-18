# Evaluation

Eval sets are data, versioned with the code and reviewable like anything else. Results are recorded
as claims by the evaluator module (`modules/evaluator`, D-71): each score in a run file
(`eval/<suite>/runs/*.json`) becomes one observed claim `urn:kk:eval:<suite>:<run> dqv:value <number>`,
qualified by `dqv:isMeasurementOf` the metric (`urn:kk:metric:<suite>:<name>`) and `dqv:computedOn` the
eval set, referencing the run file and the score in it. An agent then reads them like any claim:

```sh
DATABASE_URL=<a login that may SET ROLE kkm_evaluator> python -m evaluator --suite scifact eval/scifact/runs/*.json
```

## Running the evals with a local model

The model-backed stages call any OpenAI-compatible endpoint, so a local server works as well as a hosted
one: `KK_EVAL_MODEL_URL` (Ollama `http://localhost:11434/v1`, llama.cpp `http://localhost:8080/v1`,
vLLM, LM Studio), `KK_EVAL_MODEL` (the name the server knows the model by), `KK_EVAL_MODEL_KEY` (if it
wants one). Use the same prompt files for every model, so the numbers compare.

```sh
export KK_EVAL_MODEL_URL=http://localhost:11434/v1 KK_EVAL_MODEL=<model>
# SciFact (eval/scifact/README.md): with and without the kernel, the same 300 dev claims
AGENT_DATABASE_URL=... python eval/scifact/run.py prompts --n 300 > prompts.jsonl
AGENT_DATABASE_URL=... python eval/scifact/run.py verify --n 300 --prompts prompts.jsonl --policy first
# RCAEval (eval/rcaeval/README.md): the evidence packs, keys kept from the model
python3.12 eval/rcaeval/packs.py build DATA/RE2/RE2-OB keys.json > prompts.jsonl
python3.12 eval/rcaeval/ask.py prompts.jsonl answers.jsonl          # resumable
python3.12 eval/rcaeval/run.py --dataset re2-ob --method answers --answers answers.jsonl --keys keys.json --label <model>
```

Each run writes `runs/<date>-...json`; commit it (never the data: SciFact is CC BY-NC) and record it with
the evaluator.

## Layout

```
eval/
  <set>/
    set.yaml              # id (urn:kk:eval:<set>), slot or module under test, metrics, pass thresholds
    cases/*.yaml          # one case per file
    fixtures/             # input files the cases refer to
```

## Case format

```yaml
id: urn:kk:eval:validator/extracted-without-quote
input:
  change_set: fixtures/extracted-without-quote.json    # or: source file, chain message, question
expect:
  outcome: invalid             # applied | invalid | conflict | awaiting_review | answer
  reason: KK033                # kernel error code or shape message, when invalid
  statement_keys: []           # for applied: the claims that must exist afterwards
  cites: []                    # for answers: claim ids (or queries that resolve to them) the answer must cite
```

## Sets for milestone 1

| Set | Under test | Gate |
| --- | --- | --- |
| `validator` | validator-shacl | T4 |
| `belief-v0` | belief-corroboration-v0 | T7 |
| `self-model` | an agent over kernel-mcp: 12 questions about the system's own structure (`questions.yaml`; graded by `scripts/eval_self.py`, D-46) | G7 |

The chain sets (`runner-parity`, `round-trip`) were retired with the chain format (D-41).

The self-model set has its own format (one file, `questions.yaml`): each question names the expected
answer terms and the claim pattern that answers it. `scripts/eval_self.py` runs a deterministic
reference agent over MCP (in the kernel smoke), grades any live agent's answers (`--grade FILE`), or asks
Claude Code headless (`--claude`). Live runs are kept in `self-model/runs/` (their claim ids belong to the
database they ran against).

A slot competition is two modules run against the same set; for single-active slots the evaluator's
recorded scores decide which module is active, and the decision is logged in `docs/decisions.md`.

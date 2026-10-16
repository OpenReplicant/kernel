# Evaluation

Eval sets are data, versioned with the code and reviewable like anything else. The evaluator module
runs them and records results as observed claims about the module under test:
a DQV quality measurement (`dqv:QualityMeasurement` with `dqv:computedOn` the module, `dqv:isMeasurementOf` the metric, `dqv:value`, and the eval set as `prov:used`); the evaluator is parked (D-36, D-40).

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

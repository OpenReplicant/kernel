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
| `runner-parity` | chain-runner slot: Python vs RuleGo-Server on the same messages | G4 |
| `round-trip` | chain encoder/decoder pair | G6 |
| `self-model` | agent + kernel-mcp: questions about the system's own structure (`questions.yaml`) | G7 |

A slot competition is two modules run against the same set; for single-active slots the evaluator's
recorded scores decide which module is active, and the decision is logged in `docs/decisions.md`.

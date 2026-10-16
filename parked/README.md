# Parked

Work that is not part of the kernel (D-36), kept as it was when parked. Nothing here runs in CI or in
`deploy/compose.yaml`, and nothing here is maintained until it is revived.

| What | Was |
| --- | --- |
| `modules/chain-runner-python` | Python stand-in chain runner (T5) |
| `modules/chain-runner-rulego` | `kk-rulego`: RuleGo-Server v0.38.0 + `endpoint/pgNotify` + `x/python` (D-35) |
| `modules/chain-decoder-rulego` | chain claims → RuleGo DSL, drift check (T10, G5) |
| `modules/graph-decoder`, `modules/ui` | self-model graph and the Alpine.js UI (T9, G3) |
| `modules/evaluator` | evaluator manifest |
| `deploy/rulego` | RuleGo-Server notes |
| `scripts/smoke_stack.sh` and its gate scripts | the full-stack smoke test (T2–T10) |

To revive one: move it back under `modules/` (or `scripts/`, `deploy/`), make it talk to the kernel the
way D-36 describes (no runner required, the api schema under its own role), and bring its tests back
into CI. Paths inside these files still refer to their old locations.

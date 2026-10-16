# T10 check: do the compiled chains run on RuleGo-Server?

Date: 2026-10-16. RuleGo-Server v0.38.0, the binary built for the T1 spike.

The chain decoder's output for this repository's chains (`KK_COMPILED_DIR=... python
modules/chain-decoder-rulego/tests/test_compile.py`) was placed in a RuleGo-Server data folder
(`data/workflows/kk/rules/`) with `[global]` values for `KK_API` and the `KK_TOKEN_*` variables.

| Check | Result |
| --- | --- |
| Load | All 8 runnable chains loaded and enabled; no errors in the server log |
| Embedded schedule endpoint | `validate-change-sets` fired every 2 s (5 calls to `GET ${KK_API}/validation_queue` in 10 s) |
| Kernel token | Kernel calls carried `Authorization: Bearer ${global.KK_TOKEN_VALIDATOR}`; the call to the validator service carried none |
| Writing step with run semantics | With a stub API returning one queued change set, RuleGo sent, in order: `POST /validate` (the change set) → `POST /rpc/start_run` `{"module", "plan_step": "urn:kk:chain:validate-one/record"}` → `POST /rpc/record_validation` `{"change_set_id": "cs-1", "ok": true, "report": {...}}` → `POST /rpc/finish_run` `{"run_id": "run-uuid-1", "status": "succeeded"}` |

Not yet checked: behaviour against the real kernel, error paths and latency. That is the runner
parity comparison of T12 (gate G4).

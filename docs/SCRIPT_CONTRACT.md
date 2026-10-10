# Script contract

Every chain step is a Python script. RuleGo runs it; the script knows nothing about RuleGo.
All scripts use `pclib`, which implements this contract so each script is mostly its own logic.

## Input

One JSON object on **stdin**:

```json
{
  "run_id": "uuid",
  "step_key": "task=HumanEval/12/trial=2/actor",
  "task_id": "HumanEval/12",
  "episode": 2,
  "data": { "...": "step-specific input" },
  "meta": { "...": "pass-through metadata from the chain" }
}
```

`step_key` must be unique within a run. Build it from task, trial/episode and step name.

## Output

One JSON object on **stdout**, which becomes the next message's data:

```json
{ "data": { "...": "step-specific output" }, "meta": { "...": "optional" } }
```

Logs and diagnostics go to **stderr** only. Nothing else may be printed to stdout; `pclib`
redirects stray `print`s to stderr while the step runs, and writes the output with sorted keys
so a replayed step prints the same bytes.

## Exit codes

- `0` success
- `75` retryable failure (rate limit, transient network). Unused in v1 but keep it correct.
- anything else: permanent failure

## Idempotency

Before doing work, `pclib` checks `run.step` for `(run_id, step_key)`. If present, it prints
the stored output and exits 0 without redoing anything. On success it writes the output
(and model usage, if any) to `run.step`. A failed run is rerun from the start of its chain;
finished steps return instantly.

The step's output, its usage and its trace events commit in **one transaction**. A failed step
leaves no rows at all, so rerunning it never duplicates trace events.

## Tracing

`pclib.trace(event, slot=None, port=None, payload=None)` records a `run.trace` row with the
run, task, episode and step key from the input (written when the step commits). Unknown event
names are rejected. Mechanism tests read only this table, so any event a
mechanism test mentions must be emitted explicitly.

## Model calls

Only through `scripts/claude_call.py` / `pclib.claude()`. It:
- refuses the call if `run.run.spent_usd >= budget_usd`
- emits `llm.request` (with the full prompt in payload) and `llm.response`
- records token usage and adds cost to `spent_usd`
- reads model ids from the system context (`in:uses_model` assertions), falling back to env vars

## Fallback if process start-up is too slow

Keep this exact JSON contract but serve scripts from one long-running containerized Python
service that chains call over HTTP. Only do this if step timings show start-up rivaling the work.

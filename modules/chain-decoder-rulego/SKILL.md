---
name: kernel-chains
description: Use when writing or changing a chain (modules/*/chains/*.yaml) for the Knowledge Kernel — an automation that reacts to kernel events or a schedule, calls services, and submits change sets — so that it validates, compiles to RuleGo and records runs correctly.
---

# Writing chains

A chain is runner-neutral data (schema `kernel/schemas/chain.schema.json`). The self-encoder turns
it into claims; once a person approves those (module and chain typings take the review lane), the
chain decoder compiles them for RuleGo-Server and records `kk:runnable` and, if not, why
(`kk:notRunnableBecause`). Write chains so they stay runnable.

## Shape

```yaml
chain: urn:kk:chain:<id>            # lowercase id, unique
name: <short name>
runs_as: urn:kk:agent:<module>      # every kernel call uses this agent's token
trigger: {type: notify, spec: kernel_change_set}   # or schedule "every Ns" | http <path> | chain
steps:
- id: fetch                          # the first step is the one no other step points to
  do: http
  with: {method: GET, url: "${KK_API}/claims?change_set=eq.${msg.id}"}
  next: {success: work, failure: log_error}
```

- Step types: `http`, `subchain` (`with.chain`, optional `for_each: ${msg.<list>}`), `script`,
  `filter`, `switch`, `transform` (JavaScript, Lua or Python source; Python compiles to `x/python`),
  `log`, `mcp_tool` (not compiled yet). Labels: `success`, `failure`, `true`, `false`, or a switch's
  own labels.
- Placeholders: `${KK_API}`, `${msg}`, `${msg.a.b}`, and `${run.id}` in a step that writes to the
  kernel. A body that is exactly `${msg}` is the default and can be left out.
- Exactly one entry step; every `next` target must exist.

## Kernel writes and runs

A step that writes to the kernel (any non-GET to `${KK_API}`) gets its own kernel run, started
before the call with `plan_step` = `<chain IRI>/<step id>` and finished after it; `${run.id}` is
that run. Submitting a change set therefore looks like the shared `submit-*` chains:

```yaml
- id: submit
  do: http
  with:
    method: POST
    url: ${KK_API}/rpc/submit_change_set
    body: {cs: {idempotency_key: "${msg.idempotency_key}", attributed_to: "${msg.attributed_to}",
                generated_by: "${run.id}", target_scope: "${msg.target_scope}", ops: "${msg.ops}"}}
```

Services do the work and return change sets; chains carry them. A service never writes to the
kernel itself.

## Triggers

- `notify kernel_change_set`: starts on every change-set insert and apply with `{id, status, scope,
  lane}`; filter on `status` first, then read what you need through the API (D-32, D-35).
- `schedule every Ns` (whole seconds under a minute): periodic work only, e.g. the drift check.
- `chain`: only called as a subchain. `http`: started by a call to the runner.

## Before you submit the chain

- `python scripts/check_manifests.py` (schema) and add the file to the module's `runtime.chains`.
- `python modules/chain-decoder-rulego/tests/test_compile.py` shows which chains compile; regenerate
  `modules/pack-loader/packs.sql` (it registers the step IRIs runs refer to).

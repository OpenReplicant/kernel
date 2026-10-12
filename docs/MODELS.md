# Model access (S3)

Models are workers inside the loop (`docs/DESIGN.md`): a chain asks for a narrow, single-shot
job, and the call is resolved from the graph and recorded in it. Gate: `tests/test_models.py`.

## What the graph holds

The `models` module (`modules/models`) is an ontology and a perceiver:

```
(model_role {key: climate_chat.interpreter})
    models.uses ──▶ (model {key: claude-code-haiku})
    models.budget  {tokens: 200000} | {usd: 1.0}
    models.params  {temperature: 0}                  (optional; layered over the model's)
(model)     models.model_id "haiku", models.served_by ──▶ (endpoint {key: claude-code}),
            models.prices {input, output, cache_read, cache_write}, models.params {max_tokens: 1024}
(endpoint)  models.api anthropic | openai | claude_code, models.base_url, models.key_env
```

Each arrow is a claim, with provenance:

| Claim | Method | Source | Written by |
|---|---|---|---|
| a model's or endpoint's fields | `stated` | the catalog (`modules/models/catalog.yaml`, or one given inline), span `$.models[2].prices` | `models_catalog` (on command) |
| a role's model, budget, params | `defaulted` | the module's manifest, span `$.models.interpreter.model` | the module loader |
| a person's choice for a role | `stated` | the command, span `$.model` | `models_set_role` (on command) |

Loading an unchanged catalog writes nothing; a changed field supersedes its claim. A person's
choice supersedes the module's default and is kept when the module is reinstalled; a default
from an older version of the module is replaced by the new version's. API keys are never in the
graph: an endpoint names the environment variable that holds its key (`key_env`).

A module declares its roles in its manifest (and depends on `models`):

```yaml
models:
  interpreter:                 # role key: <module>.interpreter
    model: claude-code-haiku   # a model key from a catalog
    budget: {tokens: 200000}   # usd and/or tokens; a call needs a budget
    params: {}                 # optional
```

## The `model_call` node

`kernel/nodes/model_call.py`, an `x/python` node like the other shared nodes:

    in:  {"model_call": {"role", "prompt" | "messages", "system"?, "inputs"?}, ...}
    out: {"model_reply": {"text", "model", "usage", "cost_usd", "source"}, ...}

1. **Resolve** (`kernel.models.resolve`): the role's held `models.uses` → the model's id,
   endpoint, prices and parameters → the endpoint's API kind, URL and key variable. Exactly one
   held claim of each, or the call is refused. The ids of every claim used are kept.
2. **Budget:** spend is counted from the graph: the role's `ok` calls recorded since its held
   budget claim. At or over any limit (`usd`, `tokens`), the call is refused. A new budget claim
   starts the count afresh.
3. **Call** (`kernel.providers`): `anthropic` (Messages API through the Anthropic SDK), `openai`
   (any OpenAI-compatible chat endpoint, e.g. a local server), `claude_code` (`claude -p` on the
   logged-in Claude Code session: no tools, no settings, no MCP servers, our system prompt, an
   empty working directory).
4. **Record:** a `model_call` source, kept unchanged:
   `{role, module, at, request {system, messages}, inputs, model, model_id, endpoint, api, params,
   resolved_from, spent_before, status, reply {text, stop_reason}, served_by, usage, tokens,
   cost_usd, reported_cost_usd?, duration_ms}`. A refused or failed call is recorded too
   (`status: refused | failed`, with `error`), and the node fails.

Claims made from a reply cite the call as their source (span `$.reply.text`) and are
`inferred`. `inputs` holds what the prompt was made from (a person's words, recalled context),
so `trace()` from a belief reaches the call, and the call holds the words.

Cost is computed from the catalog's prices, per million tokens. A model without prices (a local
model, or the Claude Code subscription) costs 0; budget it in tokens. For `claude -p` the CLI's
own list-price figure is kept as `reported_cost_usd`, and tokens are summed from its
`modelUsage` (see `docs/SPIKE_NOTES.md`, S3).

## In the entity

`climate_chat` (perceiver, on command): `{text, by}` → a prompt listing the rooms the entity
knows → `model_call` (role `climate_chat.interpreter`, default Haiku through `claude -p`) →
the reply as JSON `{room, celsius}` → an inferred `climate.setpoint` (confidence 0.8) for a
known room, or nothing. The comfort reasoner picks it up on change, as it does a stated one.

```sh
curl -X POST localhost:9090/api/v1/rules/climate_chat_say/execute/COMMAND \
  -d '{"text": "could you make the lounge 22 degrees?", "by": "me"}'
curl -X POST localhost:9090/api/v1/rules/models_set_role/execute/COMMAND \
  -d '{"role": "climate_chat.interpreter", "model": "claude-sonnet-5-5", "budget": {"usd": 0.5}, "by": "me"}'
```

## Testing

The tests use the entity's real model: Haiku through `claude -p` (a few small calls a run; they
skip if `claude` is not on `PATH`). A local fake endpoint (`tests/fake_model.py`) stands in for
the `anthropic` and `openai` kinds, which need an API key or a server this environment doesn't
have; a fake `claude` (`tests/fake_claude.py`) shows the flags the CLI is run with.

## Known limits

- The budget is checked before a call and spent after it: concurrent calls for one role can
  overshoot it by the calls in flight.
- A field removed from the catalog leaves its held claim (nothing retracts it yet).
- No retries: a failed call fails the chain, and is recorded.
- `claude -p` runs where RuleGo runs (on the host), with that process's environment.

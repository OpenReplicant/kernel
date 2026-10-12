# Model routing and agent harnesses (S5)

S3 gave chains one-shot model calls (`model_call`, docs/MODELS.md). S5 widens what a catalog
entry can reach (LiteLLM) and adds agents: a model that works through tools until the task is
done, with the sandbox as its only tool. Gate: `tests/test_harnesses.py`.

```
chain ── model_call node ── providers: anthropic | openai | litellm | claude_code  (one shot, no tools)
     └── agent_run node ─── harnesses: claude_code (claude -p) ──MCP──▶ sandbox server ──▶ container
              │                                    only tool; started through env -i
              ▼
   model_call / agent_run sources in the graph (the same role, model, budget)
```

## LiteLLM

An endpoint with `api: litellm` calls LiteLLM's SDK (`pip install 'kernel[litellm]'`); the
model's `model_id` is LiteLLM's `provider/model`, and the endpoint's `base_url` its `api_base`:

```yaml
endpoints:
  - {id: openrouter, api: litellm, key_env: OPENROUTER_API_KEY}
models:
  - {id: some-model, endpoint: openrouter, model_id: openrouter/some/model, prices: {input: 0.2, output: 0.6}}
```

Tokens and the answering model come from LiteLLM's reply; cost from the catalog's prices, as
for every kind (LiteLLM's own price map is not used, nor fetched: `LITELLM_LOCAL_MODEL_COST_MAP`).
A LiteLLM **proxy** needs nothing new: it is an `openai` endpoint whose `base_url` is the proxy.

## Agent harnesses

`kernel/harnesses.py`: `run(cfg, tools) → {text, stop_reason, model, usage, transcript, tools,
turns}`, chosen by the endpoint's API kind as a provider is. `tools` are MCP servers; they are
the agent's only tools. A harness for another agent (Codex, a tool loop over LiteLLM) is one
more function in `HARNESSES` with the same contract (BACKLOG).

`claude_code` runs:

```
claude -p --model <model_id> --tools "" --strict-mcp-config --mcp-config '{"mcpServers": {"sandbox": …}}'
          --allowedTools mcp__sandbox --setting-sources "" --no-session-persistence
          --max-turns N --system-prompt … --output-format stream-json --verbose
```

in an empty temporary directory, the task on stdin: no built-in tools (no shell, files or web),
no settings, hooks or CLAUDE.md, no MCP servers but the sandbox. Claude Code starts MCP servers
with its whole environment (docs/SPIKE_NOTES.md, S5), so the sandbox server's command is
wrapped in `env -i PATH=… HOME=… PYTHONPATH=…`. The init event's tool list is recorded as what
the agent had (`tools_given`), not assumed.

## The `agent_run` node

`kernel/nodes/agent_run.py` (x/python):

    in:  {"agent_run": {"role", "task", "system"?, "inputs"?, "max_turns"?}, ...}
    out: {"agent_result": {"text", "model", "usage", "cost_usd", "turns", "tool_calls", "source"}, ...}

As `model_call`: the role resolves to a model and endpoint from held claims; the role's budget
is checked first, and model calls and agent runs for a role spend the same budget; a refused or
failed run is recorded and the node fails. The record is an `agent_run` source:

    {role, module, at, config, request {system, task, tools, max_turns}, inputs, model, model_id,
     endpoint, api, params, resolved_from, spent_before, status, reply {text, stop_reason},
     tools_given, transcript [...], turns, tool_calls, served_by, usage, tokens, cost_usd,
     reported_cost_usd?, duration_ms}

`transcript` holds every text, tool call (`name`, `input`) and tool result, in order; a failed
run keeps what was seen.

The agent's own sandbox runs are in its transcript, not separate `sandbox_run` sources: the
sandbox server records nothing. So a claim about an agent's work is made from a run the
kernel does itself. `tests/modules/coder` (chain `coder_agent`): the agent (Haiku, role
`coder.agent`) writes `solution.py` and tests it in the sandbox; the kernel then runs the
task's own tests on the final code with the `sandbox_run` node (`inputs.agent_run` = the run)
and writes `coder.passed` as `observed`, citing that run.

Startup config, section `agents` (docs/CONFIG.md): `max_turns` (a request may ask for fewer,
not more) and `timeout_s`.

## RuleGo's AI components

Built in only on request: `KERNEL_RULEGO_AI=1 tools/dev.sh up` builds `data/bin/rulego-ai`
with `-tags with_ai` (`rulego/components_ai.go`). It adds `ai/llm`, `ai/createImage` and
`ai/mcpClient`; not `ai/agent`, whose bash, file and browser tools run on the host, outside
the sandbox. What they do, run (docs/SPIKE_NOTES.md, S5):

- `ai/llm` calls an OpenAI-compatible endpoint (a LiteLLM proxy included) and returns the reply
  text alone: no tokens, no answering model, no record. It suits a prototype chain; a call
  whose result matters goes through `model_call`, which records it and counts it against a
  budget.
- `ai/mcpClient` as a node on its own never connects in this version (`MCP tool not found`).
  The kernel reaches MCP servers from `x/python` nodes (`sandbox_run`, `agent_run`).

## Known limits

- The budget is checked before a run, not during it: one agent run can overshoot by up to its
  turn limit's worth of tokens.
- `claude -p` runs where RuleGo runs (on the host) with that process's environment; the agent
  has no tool to read it, and the sandbox server doesn't get it.
- One harness so far (`claude_code`).

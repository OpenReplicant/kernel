# Startup config

`config/kernel.yaml`, or the file named in `$KERNEL_CONFIG`, read by `kernel.config.load()`.
Settings missing from the file take the defaults in `kernel/config.py`. In strings, `${python}`
is the running Python interpreter and `${root}` the repository.

| Section | What | Read by |
|---|---|---|
| `limits` | the loop guard: `max_depth`, `max_fanout`, `max_attempts` (docs/CORE.md) | `kernel.db.install_core`, which writes them into `kernel.config` |
| `sandbox` | on/off, backend, MCP server, engine, image allowlist, default and maximum limits (docs/SANDBOX.md) | the `sandbox_run` node, and the sandbox server it starts (given the file with `--config`) |

What stays out of it:

- **Secrets.** API keys and database credentials come from the environment; an endpoint in the
  model catalog names the variable holding its key (`key_env`).
- **Where the entity lives.** `DATABASE_URL`, `KERNEL_GRAPH` and `RULEGO_URL` are environment
  variables, so one config file serves a test graph and a real one.
- **Knowledge.** Models, prices and model roles are claims in the graph (docs/MODELS.md), with
  provenance, not settings.

A change takes effect where the file is read: the loop guard's limits at the next
`install_core`; the sandbox settings at the next run (each run starts its server with the
file). Each `sandbox_run` record names the config file it ran under.

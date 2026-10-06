"""What the kernel's deterministic adapters share (ADR 0027).

An adapter reads structured data (files, exports, event logs, runtime APIs) and builds a
`plan.Plan` of sources and observed claims, with no database involved. `apply` plays the
plan through the gateway's tools as an MCP client, so every write goes through
`kernel.write` and the adapter holds no database credentials. `script` renders a plan as
an eval fixture script, so `make eval` scores the mapping against a graph written by hand.
"""

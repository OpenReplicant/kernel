# 0014. Pack servers run beside the gateway, for now

Date: 2026-10-04 · Status: accepted

## Context

The design has the gateway act as the single MCP server: it would call a pack's container
as an MCP client and expose its tools under the pack's namespace (`research.search_arxiv`).
The research pack's paper-source server is the first such container. The official MCP SDK
(2.x) has no mounting or proxying; `fastmcp` 4 has both but would replace the server
library the gateway is built on, and it names mounted tools with an underscore.

## Decision

- The paper-source server (`packs/research/mcp/`, `wmk-papers`) is its own MCP server. A
  harness connects to it beside the gateway (`papers` next to `wmk`).
- It reads public metadata only and holds no database or kernel credentials. It returns
  each paper with the exact `ingest_source` arguments the research skill describes; the
  agent ingests and writes through the gateway, so the one write path is unchanged.
- Its libraries are `arxiv`, `pyalex` and `habanero` (MIT), in the `papers` dependency
  group, installed in its image only. Pacing follows each API's published limits.
- Proxying through the gateway is revisited when a second pack server exists or a harness
  allows only one server.

## Consequences

Harness configs list two servers when the research pack is used. The gateway's code and
dependencies stay unchanged. Failures from paper APIs are the pack server's problem
documents (`urn:wmk:papers:*`), separate from the kernel's.

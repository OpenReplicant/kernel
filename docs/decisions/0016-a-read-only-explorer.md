# 0016. A read-only explorer over the reader role

Date: 2026-10-04 · Status: accepted

## Context

People need to see what agents wrote: the log, the graph, why an edge is believed or
contested, and which passage each claim came from. The gateway's tools serve agents over
MCP; a browser needs table reads with joins and paging. The kernel is passive and has one
write path, so a UI must not become a second one.

## Decision

- **Read-only, opt-in.** `make up-ui` starts the compose profile `ui`, published on
  localhost only (the stack has no authentication).
- **Data through PostgREST as the reader.** PostgREST 16.4 connects as `wmk_reader`
  (role `kernel_reader`) and exposes schema `kernel`, so the explorer sees exactly what any
  reader sees: masked views (`claims_view`, `log_entries`, `query_log`), and none of the raw
  claim text or log operations that the column grants withhold. Three layers refuse writes:
  Caddy forwards only GET and HEAD, plus POST to `/rpc/`; PostgREST ends every transaction with
  a rollback; and the reader role may not execute `kernel.write`, `kernel.ingest_source`
  or `kernel.cite`.
- **Static pages, no build step.** `ui/site/` holds one HTML page, one script and one
  stylesheet with Alpine.js 3.17.4 (MIT, vendored with its npm integrity hash), served
  by Caddy 2.11.6. Both images are pinned by digest. The content security policy allows
  only the explorer's own origin; `unsafe-eval` is there because Alpine's standard build
  evaluates directives with `Function()`. Text is rendered with `x-text`, never as HTML.
- **No request logs.** URLs carry search terms and ids, so Caddy drops its per-request
  error log and PostgREST logs critical errors only (invariant 9).
- **Checked in CI.** `make ui-smoke` checks that writes are refused at both layers and that
  raw claim text and log ops are out of reach. It then loads every page in headless
  Chromium (light and dark) against the seeded stack and fails on any failed API call,
  script error or error view.

## Consequences

The explorer reads the kernel's tables and views directly, so renaming a column can break a
page. The smoke check in CI catches that. Whatever the reader role can see, the explorer
can show: widening the reader's grants widens the explorer. Editing, accounts, sharing
beyond localhost and graph drawings are not built; anything that changes the world model
goes through the gateway's tools.

## Amendment (2026-10-04)

The explorer gained a graph view and an index of models. The graph view draws a node's
neighbourhood (rings by distance), an area or the whole graph with Cytoscape.js 3.34.3
(MIT, vendored with its npm integrity hash), with a table of the same edges. Node type is
shape plus one of three validated hues (events neutral), belief is line style plus
colour. The content security policy allows one style by hash: the rule Cytoscape injects
for its container. The Models page lists the whole graph, each namespace as an area (its
size, contested edges, kinds and when it last learned something) and each `system` with
its parts and their belief ([ADR 0017](0017-software-pack-and-the-self-boundary.md)).

# 0008. The eval profile runs scripted extraction in CI

Date: 2026-10-04 · Status: accepted

## Context

The eval profile is for "CI runs of the core skill against eval fixtures". Which harness
drives it with a live model is an open question in the design, and CI has no model.

## Decision

Each fixture holds its sources, the tool calls an extractor following the core skill
makes (`script.yaml`, including rejected attempts and their fixes) and the expected
graph. `make eval` runs the gateway with the eval profile against a fresh database,
plays the script through the seven tools over MCP, scores entities and edges separately
(precision and recall) plus listed attributes, and runs the replay check on the result.

## Consequences

CI tests the kernel, gateway and pack deterministically. Extraction quality of a real
model is measured once a harness is chosen: it replaces the script with live tool calls
under the same profile, and the scoring stays as it is.

Until then, `make live` (`evals/live.py`) runs headless Claude Code against a running stack
with the interactive profile: it maps a document and a conversation, answers with citations
and replays the log, with loose checks on the resulting graph. It needs model access, so it
runs by hand, not in CI.

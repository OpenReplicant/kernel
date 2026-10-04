# 0009. Embeddings come from the gateway and are logged on create

Date: 2026-10-04 · Status: accepted

## Decision

When `WMK_EMBEDDING_URL` and `WMK_EMBEDDING_MODEL` are set, the gateway calls the
(OpenAI-compatible) endpoint before any kernel call, outside every transaction, and adds
vectors to `create` operations, to `lookup_entities` queries and to schema-slice ranking.
Vectors a model sends are dropped. The vector is part of the logged create operation, so
replay restores it. Without an endpoint, or when it fails, resolution and slicing fall
back to names and trigram matching.

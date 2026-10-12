# Spike notes (S0)

Facts checked by experiment or by reading source, with how they were obtained.

## 1. AgensGraph

- **Image:** `skaiworldwide/agensgraph:latest` pulls directly from Docker Hub in a cloud session
  (no mirror needed). Digest
  `sha256:1dccd703df570e20980693fe1407ec3af887ece29892bb7d69e5cd5a1176a4d7`, created
  2026-09-18: **AgensGraph v2.18.4.0, PostgreSQL 18.4, Alpine 3.24 (musl)**. Built without
  LLVM (no JIT bitcode); with plpython3u.
- Other tags on Docker Hub (2026-09-18): `v2.18.4.0-{alpine3.23,alpine3.24,bookworm,trixie,rocky9,rocky10}`
  and `v2.18.6.0-rc1-*`. Recipes: github.com/skaiworldwide-oss/agensgraph-docker. Source moved
  from bitnine-oss to github.com/skaiworldwide-oss/agensgraph.

## 2. pgvector (in progress)

- **Not bundled.** The image has no `vector` extension (only stock contrib plus `meta`); the
  docker recipes never mention it.
- **Expected as an external extension.** The AgensGraph docs say any Postgres extension works
  from a graph query, "which is how vector and full text search work". Regression tests
  (`cypher_typed_column.sql`, `cypher_dml.sql`) note that vector/HNSW coverage "needs the
  pgvector extension, which is not present in a plain `make check`": promoted (typed)
  properties can carry an HNSW property index, and Cypher `ORDER BY embedding <=> $1` is
  supported. That suite is not in the public repo. A web search found nothing official.
- **Install route:** distro package mirrors (dl-cdn.alpinelinux.org, deb.debian.org,
  apt.postgresql.org) are blocked from cloud sessions, so `apk add build-base` fails (HTTP
  403). Plan: compile pgvector v0.8.7 in `mirror.gcr.io/library/rust:1-alpine` (Alpine 3.24,
  has gcc and musl headers) against the AgensGraph image's server headers, then copy
  `vector.so`, the control file and SQL into the AgensGraph image.

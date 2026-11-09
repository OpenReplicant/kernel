# Postgres for the Knowledge Kernel: one image with every extension the kernel's modules and distributions use
# (D-94). The kernel schema needs nothing beyond core Postgres 16+; each extension below is only installed here,
# and only CREATEd by the module that uses it:
#   pgvector 0.8.7   recall-dense (D-61)
#   pg_cron 1.6.8    scheduled modules (needs shared_preload_libraries=pg_cron)
#   pgmq 1.13.1      durable queues between modules and harnesses (PostgreSQL License)
#   pgGraph 1.2.1    graph queries over tables, kernel.current_edge first (D-86; Apache-2.0); row-level
#                    security applies as the calling role. shared_preload_libraries=graph is optional
#                    (pre-warms a built graph at start)
ARG PG_IMAGE=docker.io/library/postgres:17.11-bookworm

# pgmq is plain SQL (no C library): its control file and scripts, from the release tag, checked against its
# commit id.
FROM ${PG_IMAGE} AS pgmq
ARG PGMQ_VERSION=1.13.1
ARG PGMQ_COMMIT=8eab18272e50077ab9f72d9787296655f2849dcc
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "v${PGMQ_VERSION}" https://github.com/pgmq/pgmq /src \
 && test "$(git -C /src rev-parse HEAD)" = "${PGMQ_COMMIT}" \
 && mkdir /out \
 && cp /src/pgmq-extension/pgmq.control /out/ \
 && cp /src/pgmq-extension/sql/pgmq.sql "/out/pgmq--${PGMQ_VERSION}.sql" \
 && cp /src/pgmq-extension/sql/pgmq--*--*.sql /out/ \
 && grep -q "default_version = '${PGMQ_VERSION}'" /out/pgmq.control

# pgGraph is a pgrx (Rust) extension. Its release images are built on postgres:17-bookworm, the same Debian
# and Postgres major as this image, so the compiled files are copied from the versioned release image rather
# than compiled here (a pgrx build adds Rust, cargo-pgrx and several minutes to every build).
# Pinned by the digest CI resolved for the tag on 2026-11-07 (PR #103).
FROM ghcr.io/evokoa/pggraph:pg17-1.2.1@sha256:2c85eb0f6b3abe5701edaa9cdb0265da56efd7bbb5202108113d02db8bd6faf5 AS pggraph

FROM ${PG_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-pgvector=0.8.7-1.pgdg12+1 postgresql-17-cron=1.6.8-1.pgdg12+2 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=pgmq /out/ /usr/share/postgresql/17/extension/
COPY --from=pggraph /usr/share/postgresql/17/extension/graph* /usr/share/postgresql/17/extension/
COPY --from=pggraph /usr/lib/postgresql/17/lib/graph.so /usr/lib/postgresql/17/lib/

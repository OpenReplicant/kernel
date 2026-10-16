# Postgres for the Knowledge Kernel. The kernel schema needs nothing beyond core Postgres 16+;
# the extensions below are installed for modules (recall-index: pgvector; trigger: pg_cron),
# and are only CREATEd by those modules' migrations. pgmq, if a module wants it, installs as
# plain SQL from its release (no extension build needed).
FROM postgres:17-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-pgvector postgresql-17-cron \
 && rm -rf /var/lib/apt/lists/*

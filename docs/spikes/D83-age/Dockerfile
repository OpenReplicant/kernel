# D-83 spike image: the kernel's Postgres 17.11 plus Apache AGE 1.7.0 (Apache-2.0; PGDG builds the voted
# PG17/v1.7.0-rc0 tag, released as 1.7.0 at downloads.apache.org/age/PG17/1.7.0).
FROM postgres:17.11-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-age=1.7.0~rc0-1.pgdg12+1 postgresql-17-pgvector=0.8.7-1.pgdg12+1 \
 && rm -rf /var/lib/apt/lists/*

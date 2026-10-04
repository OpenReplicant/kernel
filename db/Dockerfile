# Postgres 18 with Apache AGE (base image), pgvector (copied from the official
# pgvector image built for the same major and Debian release) and pg_trgm (contrib).
FROM pgvector/pgvector:0.8.7-pg18-trixie AS pgvector

FROM apache/age:release_PG18_1.8.0

COPY --from=pgvector /usr/lib/postgresql/18/lib/vector.so /usr/lib/postgresql/18/lib/
COPY --from=pgvector /usr/lib/postgresql/18/lib/bitcode/vector /usr/lib/postgresql/18/lib/bitcode/vector
COPY --from=pgvector /usr/lib/postgresql/18/lib/bitcode/vector.index.bc /usr/lib/postgresql/18/lib/bitcode/
COPY --from=pgvector /usr/share/postgresql/18/extension/vector* /usr/share/postgresql/18/extension/

# AGE must be loaded in every session that runs Cypher.
RUN echo "session_preload_libraries = 'age'" >> /usr/share/postgresql/postgresql.conf.sample

COPY db/initdb/ /docker-entrypoint-initdb.d/
COPY kernel/sql/ /opt/wmk/kernel/sql/
COPY packs/ /opt/wmk/packs/

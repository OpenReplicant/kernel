#!/bin/sh
# Build the S0 database image: fetch pgvector at a pinned tag, then docker build.
set -eu
cd "$(dirname "$0")"
PGVECTOR_VERSION=${PGVECTOR_VERSION:-v0.8.7}
rm -rf pgvector
git clone -q --depth 1 --branch "$PGVECTOR_VERSION" https://github.com/pgvector/pgvector.git pgvector
docker build -t spike-agensgraph .

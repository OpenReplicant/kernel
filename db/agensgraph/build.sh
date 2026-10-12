#!/bin/sh
# Build the database image (kernel-agensgraph): fetch pgvector at a pinned tag, then docker build.
set -eu
cd "$(dirname "$0")"
PGVECTOR_VERSION=${PGVECTOR_VERSION:-v0.8.7}
rm -rf pgvector
git clone -q --depth 1 --branch "$PGVECTOR_VERSION" https://github.com/pgvector/pgvector.git pgvector
docker build -t kernel-agensgraph .

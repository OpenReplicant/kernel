#!/bin/sh
# Runs once on first database start, after 010_authenticator.sh: installs the default modules
# (role + agent row each) from modules/*/migration.sql, mounted read-only at /kk-modules.
set -eu
for f in /kk-modules/*/migration.sql; do
  case "$f" in */_template/*) continue ;; esac
  echo "installing module: $(basename "$(dirname "$f")")"
  psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f"
done

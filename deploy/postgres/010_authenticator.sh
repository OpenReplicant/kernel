#!/bin/sh
# Runs once, after 001_kernel.sql and 002_bootstrap.sql, on first database start.
# Sets the PostgREST login password from the environment (never committed).
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v pw="$KK_AUTHENTICATOR_PASSWORD" <<'SQL'
ALTER ROLE kk_authenticator PASSWORD :'pw';
SQL

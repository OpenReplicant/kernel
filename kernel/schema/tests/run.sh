#!/usr/bin/env sh
# Runs the kernel invariant tests in a throwaway database.
# Usage: PGHOST=... PGUSER=<superuser> sh kernel/schema/tests/run.sh
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
db=${KK_TEST_DB:-kk_test}

dropdb --if-exists "$db"
for r in t_extractor t_verifier t_validator t_reviewer t_steward t_secret_readers; do
  psql -d postgres -qc "drop role if exists $r" >/dev/null
done
createdb "$db"
psql -d "$db" -v ON_ERROR_STOP=1 -q \
  -f "$here/001_kernel.sql" \
  -f "$here/002_bootstrap.sql" \
  -f "$here/tests/test_kernel.sql"

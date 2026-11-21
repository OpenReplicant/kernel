#!/usr/bin/env sh
# Runs the kernel invariant tests in throwaway databases.
# Usage: PGHOST=... PGUSER=<superuser> sh kernel/schema/tests/run.sh
#   1. a fresh kernel (001-006): the invariant tests, then the edge and applied-order tests (M6-T0, D-109), api.touched (D-112), replaced_by in scope (D-113);
#   2. a kernel made before 003, then 003-006 applied over its data, twice: edges and order filled in, safe to repeat;
#   3. concurrent applies, from two sessions (needs python3 with psycopg; skipped without it).
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
db=${KK_TEST_DB:-kk_test}

fresh() {
  dropdb --if-exists "$1"
  for r in t_extractor t_verifier t_validator t_reviewer t_steward t_secret_readers; do
    psql -d postgres -qc "drop role if exists $r" >/dev/null
  done
  createdb "$1"
}

fresh "${db}_upgrade"
psql -d "${db}_upgrade" -v ON_ERROR_STOP=1 -q \
  -f "$here/001_kernel.sql" \
  -f "$here/002_bootstrap.sql" \
  -f "$here/tests/test_kernel.sql" \
  -f "$here/003_current_edge.sql" \
  -f "$here/tests/test_upgrade_heap_prep.sql" \
  -f "$here/004_apply_retry_status_order.sql" \
  -f "$here/003_current_edge.sql" \
  -f "$here/004_apply_retry_status_order.sql" \
  -f "$here/005_touched.sql" \
  -f "$here/005_touched.sql" \
  -f "$here/006_replaced_by_scope.sql" \
  -f "$here/006_replaced_by_scope.sql" \
  -f "$here/tests/test_upgrade_heap_check.sql" \
  -f "$here/tests/test_current_edge.sql" \
  -f "$here/tests/test_touched.sql" \
  -f "$here/tests/test_replaced_by.sql"
dropdb "${db}_upgrade"

fresh "$db"
psql -d "$db" -v ON_ERROR_STOP=1 -q \
  -f "$here/001_kernel.sql" \
  -f "$here/002_bootstrap.sql" \
  -f "$here/003_current_edge.sql" \
  -f "$here/004_apply_retry_status_order.sql" \
  -f "$here/005_touched.sql" \
  -f "$here/006_replaced_by_scope.sql" \
  -f "$here/tests/test_kernel.sql" \
  -f "$here/tests/test_current_edge.sql" \
  -f "$here/tests/test_touched.sql" \
  -f "$here/tests/test_replaced_by.sql"


if python3 -c "import psycopg" 2>/dev/null; then
  KK_TEST_DB="$db" python3 "$here/tests/test_concurrent_apply.py"
else
  echo "skip: concurrent applies need python3 with psycopg"
fi

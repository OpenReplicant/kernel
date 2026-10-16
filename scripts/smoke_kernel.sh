#!/usr/bin/env sh
# Gate G4 (kernel alone): only Postgres, the validator and kernel-mcp — no Docker, no PostgREST, no
# runner. A fresh agent session submits a claim over MCP (stdio), sees it applied and cites it.
# Usage: PGHOST=localhost PGUSER=<superuser> PGPASSWORD=... sh scripts/smoke_kernel.sh
#        PYTHON: interpreter with requirements-ci.txt installed (default python3).
set -eu
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"
root=$(cd "$(dirname "$0")/.." && pwd)
db=${KK_SMOKE_DB:-kk_smoke}
py=${PYTHON:-python3}
host=${PGHOST:-localhost}

dropdb --if-exists "$db"
createdb "$db"
psql -d "$db" -v ON_ERROR_STOP=1 -q -o /dev/null \
  -f "$root/kernel/schema/001_kernel.sql" \
  -f "$root/kernel/schema/002_bootstrap.sql" \
  -f "$root/modules/validator-shacl/migration.sql" \
  -f "$root/deploy/postgres/040_people.sql" \
  -f "$root/deploy/postgres/050_agents.sql"
# A throwaway password for the login both services use to switch to their roles (never committed).
pw=$($py -c 'import secrets; print(secrets.token_hex(24))')
psql -d "$db" -v ON_ERROR_STOP=1 -q -v pw="$pw" <<'SQL'
alter role kk_authenticator password :'pw';
SQL
export DATABASE_URL="postgresql://kk_authenticator:$pw@$host:${PGPORT:-5432}/$db"

log=$(mktemp)
KK_ROLE=kkm_validator KK_SWEEP_SECONDS=5 PYTHONPATH="$root/modules/validator-shacl" \
  $py -m validator > "$log" 2>&1 &
validator=$!
cleanup() {
  status=$?
  kill "$validator" 2>/dev/null || true
  if [ "$status" -ne 0 ]; then echo "--- smoke test failed ($status); validator log:"; tail -40 "$log"; fi
  rm -f "$log"
}
trap cleanup EXIT

PYTHONPATH="$root/modules/kernel-mcp" $py "$root/scripts/e2e_kernel.py"

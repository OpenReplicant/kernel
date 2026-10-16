#!/usr/bin/env sh
# Gate G4 (kernel alone): only Postgres, the validator and kernel-mcp — no Docker, no PostgREST, no
# runner. A fresh agent session submits a claim over MCP (stdio, its own login), sees it applied and
# cites it; it cannot act as anyone else.
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
# Throwaway passwords (never committed): the services' switching login, and the agent's and the
# owner's own logins, which can act only as themselves (D-37).
pw() { $py -c 'import secrets; print(secrets.token_hex(24))'; }
auth_pw=$(pw); agent_pw=$(pw); owner_pw=$(pw)
psql -d "$db" -v ON_ERROR_STOP=1 -q -v a="$auth_pw" -v g="$agent_pw" -v o="$owner_pw" <<'SQL'
alter role kk_authenticator password :'a';
alter role agent_claude_code login password :'g';
alter role person_owner login password :'o';
SQL
url() { echo "postgresql://$1:$2@$host:${PGPORT:-5432}/$db"; }
export DATABASE_URL="$(url kk_authenticator "$auth_pw")"
export AGENT_DATABASE_URL="$(url agent_claude_code "$agent_pw")"
export OWNER_DATABASE_URL="$(url person_owner "$owner_pw")"

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

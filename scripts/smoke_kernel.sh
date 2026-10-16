#!/usr/bin/env sh
# Gate G4 (kernel alone) and T13 (ingestion): only Postgres, the validator, the belief model, kernel-mcp and
# the self-encoder command — no Docker,
# no PostgREST, no runner. A fresh agent session submits a claim over MCP (stdio, its own login), sees it applied and
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
  -f "$root/modules/belief-corroboration/migration.sql" \
  -f "$root/deploy/postgres/040_people.sql" \
  -f "$root/deploy/postgres/050_agents.sql" \
  -f "$root/modules/pack-loader/migration.sql" \
  -f "$root/modules/self-encoder/migration.sql" \
  -f "$root/modules/ingest-filter-gitleaks/migration.sql" \
  -f "$root/modules/pack-loader/packs.sql"
# Vocabulary packs installed as expected (D-23, D-40).
packs=$(psql -d "$db" -v ON_ERROR_STOP=1 -q -tA -f "$root/modules/pack-loader/tests/check_packs.sql")
echo "$packs" | tail -1
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
blog=$(mktemp)
KK_ROLE=kkm_belief KK_SWEEP_SECONDS=5 PYTHONPATH="$root/modules/belief-corroboration" \
  $py -m belief_corroboration > "$blog" 2>&1 &
belief=$!
cleanup() {
  status=$?
  kill "$validator" "$belief" 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    echo "--- smoke test failed ($status); validator log:"; tail -40 "$log"
    echo "--- belief log:"; tail -40 "$blog"
  fi
  rm -f "$log" "$blog"
}
trap cleanup EXIT

PYTHONPATH="$root/modules/kernel-mcp" $py "$root/scripts/e2e_kernel.py"
# T13: ingestion without a runner (needs gitleaks on PATH for the ingest filter).
PYTHONPATH="$root/modules/self-encoder:$root/modules/ingest-filter-gitleaks" $py "$root/scripts/e2e_ingest.py"

#!/usr/bin/env sh
# Gate G4 (kernel alone), T13 (ingestion) and T17 (kk alone): a fresh database set up and run with
# the `kk` command only — no Docker, no runner. A fresh agent session submits a claim over MCP (stdio,
# its own login), sees it applied and cites it; it cannot act as anyone else.
# Usage: PGHOST=localhost PGUSER=<superuser> PGPASSWORD=... sh scripts/smoke_kernel.sh
#        PYTHON: interpreter with this repository installed (pip install -e .; default python3).
#        gitleaks on PATH (the ingest filter).
set -eu
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"
root=$(cd "$(dirname "$0")/.." && pwd)
db=${KK_SMOKE_DB:-kk_smoke}
py=${PYTHON:-python3}
host=${PGHOST:-localhost}
kk() { $py -m kk "$@"; }
pw() { $py -c 'import secrets; print(secrets.token_hex(24))'; }
field() { $py -c "import json, sys; print(json.load(sys.stdin)['$1'])"; }

dropdb --if-exists "$db"
createdb "$db"
# Throwaway secrets (never committed).
auth_pw=$(pw)
export KK_ADMIN_URL="dbname=$db" KK_JWT_SECRET="$(pw)$(pw)"
KK_AUTHENTICATOR_PASSWORD="$auth_pw" kk init > /dev/null
# Vocabulary packs installed as expected (D-23, D-40).
packs=$(psql -d "$db" -v ON_ERROR_STOP=1 -q -tA -f "$root/modules/pack-loader/tests/check_packs.sql")
echo "$packs" | tail -1
# The owner's and the agent's own logins, each able to act only as itself (D-37).
url() { echo "postgresql://$1:$2@$host:${PGPORT:-5432}/$db"; }
owner_pw=$(kk person add owner --steward | field password)
export OWNER_DATABASE_URL="$(url person_owner "$owner_pw")"
export AGENT_DATABASE_URL="$(url agent_claude_code "$(kk agent add claude-code --label 'Claude Code (harness agent)' | field password)")"
export DATABASE_URL="$(url kk_authenticator "$auth_pw")"
admin_url=$KK_ADMIN_URL
unset KK_ADMIN_URL

port=$($py -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
export KK_MCP_URL="http://127.0.0.1:$port/mcp"
log=$(mktemp)
# D-61: dense recall when Postgres has pgvector and python has fastembed (KK_REQUIRE_DENSE=1 makes their
# absence a failure, as in CI); KK_RERANK=1: the dense worker also downloads the cross-encoder (D-66).
parts=mcp,validator,belief,recall,quotes
# M3-T6: the quote verifier reads file: sources under the repository and artifacts from a fresh store.
export KK_SOURCE_ROOT="$root" KK_ARTIFACTS="$(mktemp -d)"
if [ "$(psql -d "$db" -tAc "select to_regclass('recall_dense.vec') is not null")" = t ] \
   && $py -c 'import fastembed' 2> /dev/null; then
  parts=$parts,dense
elif [ "${KK_REQUIRE_DENSE:-0}" = 1 ]; then
  echo "FAIL dense recall needs pgvector in Postgres and fastembed (pip install -e '.[dense]')"; exit 1
fi
KK_RERANK=1 KK_SWEEP_SECONDS=5 $py -m kk serve --port "$port" --only "$parts" > "$log" 2>&1 &
serve=$!
cleanup() {
  status=$?
  kill "$serve" 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    echo "--- smoke test failed ($status); kk serve log:"; tail -60 "$log"
  fi
  rm -f "$log"
}
trap cleanup EXIT

$py "$root/scripts/e2e_kernel.py"
# T13: ingestion without a runner (needs gitleaks on PATH for the ingest filter).
$py "$root/scripts/e2e_ingest.py"
# Milestone 2, BPM: event-log encoder (M2-T1).
$py "$root/scripts/e2e_bpm.py"
# T16 and G7: the self-questions, answered over MCP by the reference agent (eval/self-model).
$py "$root/scripts/eval_self.py"
KK_DATABASE_URL="$OWNER_DATABASE_URL" kk status > /dev/null
echo "T17 kk alone passed"
# T19: export and import round trip into a fresh kernel.
KK_SMOKE_DB="$db" $py "$root/scripts/e2e_transfer.py"
# T20 and G3: the UI in headless Chromium (needs Node and the playwright package; KK_REQUIRE_UI=1 makes
# a missing browser a failure, as in CI).
if (cd "$root" && node -e "require('playwright')" 2> /dev/null); then
  KK_URL="http://127.0.0.1:$port" KK_OWNER_PASSWORD="$owner_pw" \
    KK_OWNER_TOKEN="$(kk token person_owner --days 0.01)" \
    KK_AGENT_TOKEN="$(kk token agent_claude_code --days 0.01)" node "$root/scripts/e2e_ui.mjs"
elif [ "${KK_REQUIRE_UI:-0}" = 1 ]; then
  echo "FAIL the UI check needs Node and playwright (npm install --no-save playwright)"; exit 1
else
  echo "skip the UI check (no playwright; npm install --no-save playwright to run it)"
fi
# D-60: the recall index (BM25) that kk serve keeps; scopes and redaction respected.
KK_ADMIN_URL="$admin_url" $py "$root/scripts/e2e_recall.py"
# D-62: belief from works that support or refute a proposition.
$py "$root/scripts/e2e_evidence.py"
# M3-T6: the quote verifier catches a fabricated quote; belief caps it (D-67).
$py "$root/scripts/e2e_quotes.py"
# M3-G2: an answer citing the kernel is checked against the claims it cites; eval scores are claims (D-71).
$py "$root/scripts/e2e_g2.py"
# D-61: dense recall fused with BM25.
case $parts in
  *dense) KK_ADMIN_URL="$admin_url" $py "$root/scripts/e2e_dense.py" ;;
  *) echo "skip dense recall (no pgvector or fastembed)" ;;
esac
# M3-T1: kk up connects a project folder (last: it gives the owner a new login).
KK_ADMIN_URL="$admin_url" $py "$root/scripts/e2e_up.py"
# M3-G1: zero to a cited answer on your own repository (clone, kk up, the harness's hooks).
KK_ADMIN_URL="$admin_url" $py "$root/scripts/e2e_g1.py"

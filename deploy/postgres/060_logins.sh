#!/bin/sh
# Runs once on first database start, after 050_agents.sql: gives harness agents and people their own
# logins, each able to act only as itself (no role switching), from passwords in the environment
# (never committed). A role whose password is unset stays nologin. D-37.
set -eu
login() {  # role, password
  [ -n "$2" ] || return 0
  psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v role="$1" -v pw="$2" <<'SQL'
ALTER ROLE :"role" LOGIN PASSWORD :'pw';
SQL
}
login agent_claude_code "${KK_AGENT_CLAUDE_CODE_PASSWORD:-}"
login person_owner "${KK_OWNER_PASSWORD:-}"

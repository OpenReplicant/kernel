#!/usr/bin/env sh
# T2 gate: bring up db, postgrest and caddy with throwaway secrets; GET /api/claims must work with
# a module JWT and fail without one. Tears the stack down afterwards (volumes included).
# Usage: sh scripts/smoke_t2.sh        (needs docker compose or podman compose, python3, curl)
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
env_file=$(mktemp)
compose="docker compose -p kk-smoke -f $root/deploy/compose.yaml --env-file $env_file"
trap '$compose down -v >/dev/null 2>&1; rm -f "$env_file"' EXIT

rand() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(rand)/" \
    -e "s/^KK_AUTHENTICATOR_PASSWORD=.*/KK_AUTHENTICATOR_PASSWORD=$(rand)/" \
    -e "s/^KK_JWT_SECRET=.*/KK_JWT_SECRET=$(rand)/" \
    "$root/deploy/.env.example" > "$env_file"

$compose up -d --build --wait db postgrest caddy

KK_JWT_SECRET=$(sed -n 's/^KK_JWT_SECRET=//p' "$env_file")
token=$(KK_JWT_SECRET=$KK_JWT_SECRET python3 "$root/scripts/mint_jwt.py" kkm_validator --days 0.01)
url=http://127.0.0.1:8080/api/claims

# PostgREST may need a moment after the db reports healthy.
i=0; until curl -s -o /dev/null "$url" || [ $i -ge 30 ]; do i=$((i+1)); sleep 1; done

with=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "$url")
without=$(curl -s -o /dev/null -w '%{http_code}' "$url")
forged=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(KK_JWT_SECRET=wrong-secret-wrong-secret-wrong-secret python3 "$root/scripts/mint_jwt.py" kkm_validator)" "$url")
agents=$($compose exec -T db psql -U postgres -d kk -tAc "select count(*) from kernel.agent where db_role like 'kkm\_%'")

echo "with module JWT: $with; without: $without; forged JWT: $forged; module agents: $agents"
[ "$with" = 200 ] && [ "$without" != 200 ] && [ "$forged" != 200 ] && [ "$agents" -ge 11 ]
echo "T2 smoke test passed"

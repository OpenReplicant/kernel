#!/usr/bin/env sh
# Stack smoke test with throwaway secrets; tears the stack down afterwards (volumes included).
#   T2: db, postgrest and caddy come up; GET /api/claims works with a module JWT, fails without one.
#   T3: packs and slot catalog installed on first start (modules/pack-loader/tests/check_packs.sql),
#       and a module sees the 42 slots through the API.
#   T4: the validator container judges submitted change sets end to end (scripts/e2e_validator.py).
#   T5: the Python runner's validator chains validate and record new change sets on their own
#       (scripts/e2e_runner.py); each recorded verdict has a kernel run linked to its step IRI.
# Usage: sh scripts/smoke_stack.sh        (needs docker compose, python3, curl)
#        KK_BUILD_CA=/path/to/ca.crt sh scripts/smoke_stack.sh   (behind a TLS-intercepting proxy)
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
env_file=$(mktemp)
compose="docker compose -p kk-smoke -f $root/deploy/compose.yaml --env-file $env_file"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- smoke test failed ($status); module logs:"
    $compose logs --tail 40 validator chain-runner-python 2>/dev/null || true
  fi
  $compose down -v >/dev/null 2>&1; rm -f "$env_file"
}
trap cleanup EXIT

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

$compose exec -T db psql -v ON_ERROR_STOP=1 -q -U postgres -d kk < "$root/modules/pack-loader/tests/check_packs.sql"
slots=$(curl -s -H "Authorization: Bearer $token" \
  "$url?predicate=eq.http://www.w3.org/1999/02/22-rdf-syntax-ns%23type&object_entity=eq.https://w3id.org/knowledge-kernel/ns%23Slot&select=subject" |
  python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
echo "slots visible to a module through the API: $slots"
[ "$slots" = 42 ]
echo "T3 smoke test passed"

sed -i "s/^KK_JWT_VALIDATOR=.*/KK_JWT_VALIDATOR=$token/" "$env_file"
# KK_BUILD_CA: optional CA bundle for pip inside the build (machines behind a TLS-intercepting proxy).
if [ -n "${KK_BUILD_CA:-}" ]; then
  for m in validator:validator-shacl chain-runner-python:chain-runner-python; do
    docker build -q --secret id=ca,src="$KK_BUILD_CA" -f "$root/modules/${m#*:}/Dockerfile" \
      -t "kk-smoke-${m%%:*}" "$root" >/dev/null
  done
else
  $compose build validator chain-runner-python
fi
$compose up -d --no-build --wait validator
$compose exec -T \
  -e AGENT_JWT="$(KK_JWT_SECRET=$KK_JWT_SECRET python3 "$root/scripts/mint_jwt.py" kkm_self_encoder --days 0.01)" \
  -e VALIDATOR_JWT="$token" validator python - < "$root/scripts/e2e_validator.py"

agent_token=$(KK_JWT_SECRET=$KK_JWT_SECRET python3 "$root/scripts/mint_jwt.py" kkm_self_encoder --days 0.01)
$compose up -d --no-build --wait chain-runner-python
AGENT_JWT=$agent_token python3 "$root/scripts/e2e_runner.py"
runs=$($compose exec -T db psql -U postgres -d kk -tAc "select count(*) from kernel.run
  where plan_step = 'urn:kk:chain:validate-one/record' and agent = 'urn:kk:agent:validator'
    and module = 'urn:kk:module:validator-shacl' and status = 'succeeded'")
echo "validator runs linked to urn:kk:chain:validate-one/record: $runs"
[ "$runs" -ge 2 ]
echo "T5 smoke test passed"

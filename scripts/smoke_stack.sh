#!/usr/bin/env sh
# Stack smoke test with throwaway secrets; tears the stack down afterwards (volumes included).
#   T2: db, postgrest and caddy come up; GET /api/claims works with a module JWT, fails without one.
#   T3: packs and slot catalog installed on first start (modules/pack-loader/tests/check_packs.sql),
#       and a module sees the 42 slots through the API.
#   T4: the validator container judges submitted change sets end to end (scripts/e2e_validator.py).
#   T5: the Python runner's validator chains validate and record new change sets on their own
#       (scripts/e2e_runner.py); each recorded verdict has a kernel run linked to its step IRI.
#   T6/G2: the scan chain filters and encodes this repository; the owner approves the review lane;
#       scripts/check_g2.py and the SQL below check the self-model is complete.
# PYTHON: interpreter with pyyaml, rdflib and pyshacl for check_g2.py (default python3).
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

mint() { KK_JWT_SECRET=$KK_JWT_SECRET python3 "$root/scripts/mint_jwt.py" "$1" --days 0.02; }
sed -i -e "s/^KK_JWT_VALIDATOR=.*/KK_JWT_VALIDATOR=$token/" \
       -e "s/^KK_JWT_SELF_ENCODER=.*/KK_JWT_SELF_ENCODER=$(mint kkm_self_encoder)/" \
       -e "s/^KK_JWT_INGEST_FILTER=.*/KK_JWT_INGEST_FILTER=$(mint kkm_ingest_filter)/" "$env_file"
# KK_BUILD_CA: optional CA bundle for pip inside the build (machines behind a TLS-intercepting proxy).
if [ -n "${KK_BUILD_CA:-}" ]; then
  for m in validator:validator-shacl chain-runner-python:chain-runner-python \
           self-encoder:self-encoder ingest-filter:ingest-filter-gitleaks; do
    docker build -q --secret id=ca,src="$KK_BUILD_CA" -f "$root/modules/${m#*:}/Dockerfile" \
      -t "kk-smoke-${m%%:*}" "$root" >/dev/null
  done
else
  $compose build validator chain-runner-python self-encoder ingest-filter
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

# T6: scan this repository into the self-model.
$compose up -d --no-build --wait self-encoder ingest-filter
$compose exec -T chain-runner-python python -c '
import json, urllib.request
req = urllib.request.Request("http://localhost:8000/trigger?chain=urn:kk:chain:scan-self", data=b"{}", method="POST")
ex = json.load(urllib.request.urlopen(req, timeout=300))
print("scan ok:", ex["ok"], "runs:", len(ex["runs"]), "steps:", [t["step"].rsplit("/", 1)[1] + ":" + t["label"] for t in ex["trace"]])
assert ex["ok"], json.dumps(ex["trace"])[:2000]'
# The filter blocks a file with a secret (generated here, never committed) and passes a clean one.
$compose exec -T -e KK_REPO=/tmp/leak ingest-filter python -c '
import json, os, secrets, string
os.makedirs("/tmp/leak", exist_ok=True)
token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
open("/tmp/leak/config.yaml", "w").write("github_token: " + token + "\n")
open("/tmp/leak/clean.yaml", "w").write("name: nothing to see\n")
from ingest_filter.__main__ import scan
out = scan(["config.yaml", "clean.yaml"])
print("ingest filter:", json.dumps(out))
assert out["paths"] == ["clean.yaml"] and out["blocked"][0]["path"] == "config.yaml", out
assert token not in json.dumps(out)'
pending() { $compose exec -T db psql -U postgres -d kk -tAc \
  "select count(*) from kernel.change_set where idempotency_key like 'self-encoder:%' and status in ($1)"; }
i=0; until [ "$(pending "'submitted'")" = 0 ] || [ $i -ge 60 ]; do i=$((i+1)); sleep 1; done
owner=$(mint person_owner)
scan_ids=$($compose exec -T db psql -U postgres -d kk -tAc "select id from kernel.change_set
  where idempotency_key like 'self-encoder:%' and status = 'awaiting_review'")
KK_URL=http://127.0.0.1:8080/api KK_TOKEN=$owner python3 "$root/scripts/review.py" approve $scan_ids \
  --note "smoke test: self-model" | tail -1
$compose exec -T db psql -U postgres -d kk -tAc "select 'scan change sets ' || status, count(*) from kernel.change_set
  where idempotency_key like 'self-encoder:%' group by status order by status"
[ "$(pending "'submitted','valid','awaiting_review','invalid','conflict'")" = 0 ]
KK_URL=http://127.0.0.1:8080/api KK_TOKEN=$agent_token ${PYTHON:-python3} "$root/scripts/check_g2.py"
modules=$($compose exec -T db psql -U postgres -d kk -tAc "select count(distinct subject) from kernel.claim
  where predicate = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
    and object_entity = 'https://w3id.org/knowledge-kernel/ns#Module'")
echo "modules in the self-model: $modules (manifests: $(ls -d "$root"/modules/*/module.yaml | grep -vc _template))"
[ "$modules" = "$(ls -d "$root"/modules/*/module.yaml | grep -vc _template)" ]
unreferenced=$($compose exec -T db psql -U postgres -d kk -tAc "select count(*) from kernel.claim c
  where not exists (select 1 from kernel.claim_reference r where r.claim_id = c.id)
    and not exists (select 1 from kernel.claim_derivation d where d.claim_id = c.id)")
unused=$($compose exec -T db psql -U postgres -d kk -tAc "select count(*) from kernel.artifact a
  where not exists (select 1 from kernel.claim_reference r where r.artifact = a.sha256)")
echo "claims without a reference: $unreferenced; artifacts no claim references: $unused"
[ "$unreferenced" = 0 ] && [ "$unused" = 0 ]
echo "T6 smoke test passed (G2)"

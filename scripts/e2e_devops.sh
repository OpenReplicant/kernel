#!/usr/bin/env sh
# Milestone 2, devops: the demo stack (a service under load, Prometheus) next to the kernel, and the
# metrics observer installed as a sandboxed module that reaches Prometheus and nothing else (D-52).
# M2-T5: windowed observations for each service, linked to the self-model's nodes.
# M2-T6: the MAPE-K loop — the shop is stressed, the anomaly processor claims the anomaly, an agent plans
# a scale-out (review lane), the owner approves, the actuator scales through the socket proxy, and the
# observer records the recovery; every step cites the one before.
# M2-T7: the Devops view shows that run's timeline (KK_REQUIRE_UI=1 makes a missing browser a failure).
# Needs Docker, the kk image (knowledge-kernel:local; built if missing) and this repository installed.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-python3}
project=kk-e2e-devops
env=$(mktemp); out=$(mktemp -d); log=$(mktemp)
pw() { $py -c 'import secrets; print(secrets.token_hex(24))'; }
port=$($py -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
cat > "$env" <<ENV
POSTGRES_PASSWORD=$(pw)
KK_AUTHENTICATOR_PASSWORD=$(pw)
KK_JWT_SECRET=$(pw)$(pw)
CADDY_IMAGE=caddy:2.11.7-alpine
KK_DB_PORT=$port
DOCKER_GID=$(stat -c %g /var/run/docker.sock)
KK_DEMO_DIR=$root/deploy/demo
ENV
pg_pw=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$env")
auth_pw=$(sed -n 's/^KK_AUTHENTICATOR_PASSWORD=//p' "$env")
files="-f $root/deploy/compose.yaml -f $root/deploy/demo/compose.yaml"
dc() { docker compose -p "$project" --env-file "$env" $files "$@"; }
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then tail -30 "$log"; dc logs --tail 30 module-observer-prometheus prometheus 2>/dev/null || true; fi
  dc down -v --remove-orphans > /dev/null 2>&1 || true
  rm -rf "$env" "$out" "$log"
}
trap cleanup EXIT
docker image inspect knowledge-kernel:local > /dev/null 2>&1 || docker build -q -t knowledge-kernel:local "$root"

dc up -d --wait db >> "$log" 2>&1
dc up -d kernel shop loadgen prometheus docker-proxy >> "$log" 2>&1
export KK_ADMIN_URL="postgresql://postgres:$pg_pw@127.0.0.1:$port/kk"
# The self-model: the self-encoder reads the compose files, the demo's included (kk scan, as itself).
DATABASE_URL="postgresql://kk_authenticator:$auth_pw@127.0.0.1:$port/kk" KK_ROLE=kkm_self_encoder \
  $py -m kk scan --repo "$root" --artifacts "$out/artifacts" >> "$log" 2>&1
field() { $py -c "import json, sys; print(json.load(sys.stdin)['$1'])"; }
url() { echo "postgresql://$1:$2@127.0.0.1:$port/kk"; }
export OWNER_DATABASE_URL="$(url person_owner "$($py -m kk person add owner --steward | field password)")"
export AGENT_DATABASE_URL="$(url agent_planner "$($py -m kk agent add planner --label 'Planning agent' | field password)")"
for m in observer-prometheus anomaly actuator-compose; do
  $py -m kk module install "$root/modules/$m" --out "$out" > /dev/null
  files="$files -f $out/$m.yaml"
done
echo "KK_WINDOW_SECONDS=10" >> "$out/observer-prometheus.env"
printf 'KK_SWEEP_SECONDS=5\n' >> "$out/anomaly.env"
printf 'KK_SWEEP_SECONDS=5\nKK_COMPOSE_PROJECT=%s\nKK_ACTUATOR_SERVICES=shop\nKK_MAX_REPLICAS=4\n' "$project" \
  >> "$out/actuator-compose.env"
dc up -d module-observer-prometheus module-anomaly module-actuator-compose >> "$log" 2>&1
export KK_DC="docker compose -p $project --env-file $env $files"
KK_CONTAINER=$(dc ps -q module-observer-prometheus) $py "$root/scripts/e2e_devops.py"
# M2-T7: the Devops view of this run, through Caddy (needs Node and playwright, as the UI smoke does).
if (cd "$root" && node -e "require('playwright')" 2> /dev/null); then
  dc up -d caddy >> "$log" 2>&1
  i=0; until $py -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ui/views.json', timeout=2)" 2> /dev/null; do
    i=$((i + 1)); [ "$i" -lt 60 ] || { echo "FAIL Caddy did not answer"; exit 1; }; sleep 1
  done
  token=$(KK_JWT_SECRET=$(sed -n 's/^KK_JWT_SECRET=//p' "$env") $py -m kk token person_owner --days 0.01)
  KK_URL=http://127.0.0.1:8080 KK_OWNER_TOKEN="$token" node "$root/scripts/e2e_devops_ui.mjs"
elif [ "${KK_REQUIRE_UI:-0}" = 1 ]; then
  echo "FAIL the devops UI check needs Node and playwright (npm install --no-save playwright)"; exit 1
else
  echo "skip the devops UI check (no playwright)"
fi

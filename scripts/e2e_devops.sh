#!/usr/bin/env sh
# Milestone 2, devops: the demo stack (a service under load, Prometheus) next to the kernel, and the
# metrics observer installed as a sandboxed module that reaches Prometheus and nothing else (D-52).
# M2-T5: windowed observations for each service, linked to the self-model's nodes.
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
dc up -d kernel shop loadgen prometheus >> "$log" 2>&1
export KK_ADMIN_URL="postgresql://postgres:$pg_pw@127.0.0.1:$port/kk"
# The self-model: the self-encoder reads the compose files, the demo's included (kk scan, as itself).
DATABASE_URL="postgresql://kk_authenticator:$auth_pw@127.0.0.1:$port/kk" KK_ROLE=kkm_self_encoder \
  $py -m kk scan --repo "$root" --artifacts "$out/artifacts" >> "$log" 2>&1
$py -m kk module install "$root/modules/observer-prometheus" --out "$out" > /dev/null
echo "KK_WINDOW_SECONDS=10" >> "$out/observer-prometheus.env"
files="$files -f $out/observer-prometheus.yaml"
dc up -d module-observer-prometheus >> "$log" 2>&1
KK_CONTAINER=$(dc ps -q module-observer-prometheus) $py "$root/scripts/e2e_devops.py"

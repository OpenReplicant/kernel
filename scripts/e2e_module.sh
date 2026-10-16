#!/usr/bin/env sh
# T18: installing a module from its folder gives a running, sandboxed worker (and, M2-G3, a command). Needs Docker, the kk image
# (knowledge-kernel:local; built if missing) and this repository installed (pip install -e .).
# The kernel container runs MCP and the validator only; the belief model runs as an installed module
# in its own container. Checks: it scores a claim; its filesystem is read-only; it has no capabilities
# and no route out; its login acts only as itself.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-python3}
project=kk-e2e-module
env=$(mktemp); out=$(mktemp -d)
files="-f $out/belief-corroboration.yaml"   # compose files of the installed modules (for cleanup too)
pw() { $py -c 'import secrets; print(secrets.token_hex(24))'; }
port=$($py -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
cat > "$env" <<ENV
POSTGRES_PASSWORD=$(pw)
KK_AUTHENTICATOR_PASSWORD=$(pw)
KK_JWT_SECRET=$(pw)$(pw)
CADDY_IMAGE=caddy:2.11.7-alpine
KK_DB_PORT=$port
KK_SERVE_PARTS=mcp,validator
ENV
pg_pw=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$env")
log=$(mktemp)
dc() { docker compose -p "$project" --env-file "$env" -f "$root/deploy/compose.yaml" "$@"; }
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    tail -20 "$log"; dc -f "$out/belief-corroboration.yaml" logs --tail 40 2>/dev/null || dc logs --tail 40 || true
  fi
  KK_INPUT=/nonexistent dc ${files:-} down -v > /dev/null 2>&1 || dc down -v --remove-orphans > /dev/null 2>&1 || true
  rm -rf "$env" "$out" "$log"
}
trap cleanup EXIT
docker image inspect knowledge-kernel:local > /dev/null 2>&1 || docker build -q -t knowledge-kernel:local "$root"

dc up -d --wait db >> "$log" 2>&1
dc up -d kernel >> "$log" 2>&1   # runs init first
export KK_ADMIN_URL="postgresql://postgres:$pg_pw@127.0.0.1:$port/kk"
# A module outside the default distribution's licenses (D-11) is refused.
mkdir "$out/gpl-module" && sed 's/^license: .*/license: GPL-3.0-only/' "$root/modules/belief-corroboration/module.yaml" \
  > "$out/gpl-module/module.yaml"
if $py -m kk module install "$out/gpl-module" --out "$out" 2> "$log.err"; then
  echo "FAIL a GPL module was installed"; exit 1
fi
grep -q "allow list" "$log.err" && echo "ok   a module with a license outside the allow list is refused"
rm -f "$log.err"
$py -m kk module install "$root/modules/belief-corroboration" --out "$out" > /dev/null
dc -f "$out/belief-corroboration.yaml" up -d module-belief-corroboration >> "$log" 2>&1
cid=$(dc -f "$out/belief-corroboration.yaml" ps -q module-belief-corroboration)
KK_CONTAINER=$cid $py "$root/scripts/e2e_module.py"
# M2-G3: command modules install the same way and run one-shot in their own locked-down containers,
# reading their input read-only (the encoders on the sample order-to-cash files).
command_module() {  # NAME ROLE ARGS...
  name=$1; role=$2; shift 2
  $py -m kk module install "$root/modules/$name" --out "$out" > /dev/null
  files="$files -f $out/$name.yaml"
  res=$(KK_INPUT="$root/examples/order-to-cash" dc $files run --rm -T "module-$name" "$@" 2>> "$log")
  KK_RESULT="$res" KK_NAME="$name" KK_ROLE="$role" $py - <<'PY'
import json, os, sys, time
import psycopg
res = json.loads(os.environ["KK_RESULT"].strip().splitlines()[-1])
with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as conn:
    for _ in range(120):
        status, by = conn.execute("select status, submitted_by from kernel.change_set where id = %s",
                                  (res["change_set"],)).fetchone()
        if status != "submitted":
            break
        time.sleep(0.5)
ok = status == "applied" and by == os.environ["KK_ROLE"]
print(("ok  " if ok else "FAIL"), f"{os.environ['KK_NAME']} runs sandboxed as itself and its change set is applied",
      f"({status}, submitted by {by}, {res['claims']} claims)")
sys.exit(0 if ok else 1)
PY
}
command_module encoder-eventlog kkm_encoder_eventlog --process urn:kk:process:order-to-cash /input/order-to-cash.csv
command_module encoder-bpmn kkm_encoder_bpmn --process urn:kk:process:order-to-cash /input/order-to-cash.bpmn
echo "M2-G3 command modules passed"

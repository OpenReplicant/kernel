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
  KK_INPUT=/nonexistent dc ${files:-} --profile run down -v > /dev/null 2>&1 || dc down -v --remove-orphans > /dev/null 2>&1 || true
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
# A module that asks for network the sandbox policy does not grant is refused (D-55).
echo 'defaults: {reach: []}' > "$out/policy.yaml"
if $py -m kk module install "$root/modules/observer-prometheus" --out "$out" --policy "$out/policy.yaml" 2> "$log.err"; then
  echo "FAIL a module was installed with network the policy does not grant"; exit 1
fi
grep -q "reach prometheus" "$log.err" && echo "ok   a module asking for network the sandbox policy does not grant is refused"
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
# The conformance processor is a worker: installed the same way, it compares the two and submits the
# discrepancies from its own container, each to the review lane.
$py -m kk module install "$root/modules/conformance" --out "$out" > /dev/null
files="$files -f $out/conformance.yaml"
KK_INPUT=/nonexistent dc $files up -d module-conformance >> "$log" 2>&1
$py - <<'PY'
import os, sys, time
import psycopg
with psycopg.connect(os.environ["KK_ADMIN_URL"], autocommit=True) as conn:
    for _ in range(120):
        n = conn.execute("""select count(*) from kernel.change_set where submitted_by = 'kkm_conformance'
                            and lane = 'review' and status = 'awaiting_review'""").fetchone()[0]
        if n >= 5:
            break
        time.sleep(1)
ok = n == 5      # the planted skips, swap and rework; the time limit needs the written procedure
print(("ok  " if ok else "FAIL"), "the conformance worker runs sandboxed and submits the discrepancies for review",
      f"({n} awaiting review)")
sys.exit(0 if ok else 1)
PY
echo "M2-G3 command and worker modules passed"
# M3-G3: the milestone-3 modules install the same way under the default policy. The paper encoder runs
# one-shot on a small file of its own; the recall index and the quote verifier (reading the artifact
# store read-only) run as workers; recall-dense installs (its worker needs a glibc image, D-70). M4-G2: the
# process learner (milestone 4) runs as a worker too; M5-G3: the interaction encoder encodes an interview and the
# map gap processor runs as a worker.
papers="$out/papers"; mkdir "$papers"
cat > "$papers/papers.jsonl" <<'JSONL'
{"_id": "1", "title": "Sandboxed module workers", "text": "Sandboxed workers read the artifact store without writing it. Each runs as its own database role."}
{"_id": "2", "title": "Network grants", "text": "An operator grants each module the services it may reach; a module asking for more is refused."}
JSONL
chmod 755 "$papers"; chmod 644 "$papers/papers.jsonl"
for name in encoder-papers recall-index verifier-quotes recall-dense learner-process encoder-interaction map-gaps; do
  $py -m kk module install "$root/modules/$name" --out "$out" > /dev/null
done
files="$files -f $out/encoder-papers.yaml -f $out/recall-index.yaml -f $out/verifier-quotes.yaml -f $out/recall-dense.yaml -f $out/learner-process.yaml -f $out/encoder-interaction.yaml -f $out/map-gaps.yaml"
res=$(KK_INPUT="$papers" dc $files run --rm -T module-encoder-papers --source g3 /input/papers.jsonl 2>> "$log")
chats="$out/chats"; mkdir "$chats"; cp "$root/modules/encoder-interaction/tests/fixtures/interview.jsonl" "$chats/"
chmod 755 "$chats"; chmod 644 "$chats/interview.jsonl"
chat=$(KK_INPUT="$chats" dc $files run --rm -T module-encoder-interaction --source g3 /input/interview.jsonl 2>> "$log")
KK_INPUT=/nonexistent dc $files up -d module-recall-index module-verifier-quotes module-learner-process module-map-gaps >> "$log" 2>&1
KK_RESULT="$res" KK_CHAT_RESULT="$chat" KK_PAPERS="$papers/papers.jsonl" $py "$root/scripts/e2e_module_m3.py"

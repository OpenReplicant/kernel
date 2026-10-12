#!/bin/sh
# Bring up the S0 spike from scratch: database container, graph + outbox, RuleGo-Server on
# the host (so x/python steps use the repo venv), the on-change chain. Then:
#   DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres PATH=$REPO/.venv/bin:$PATH \
#     pytest spike/test_gate.py
set -eu
SPIKE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$SPIKE")
S=${S0_STATE:-/tmp/claude-0/s0}
export DATABASE_URL=postgresql://agens@127.0.0.1:55432/postgres
export PATH="$REPO/.venv/bin:$PATH"

docker image inspect spike-agensgraph >/dev/null 2>&1 || "$SPIKE/agensgraph/build.sh"
docker rm -f s0db >/dev/null 2>&1 || true
docker run -d --name s0db -p 55432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_PASSWORD=unused spike-agensgraph >/dev/null
until pg_isready -q -d "$DATABASE_URL"; do sleep 0.5; done
sleep 1  # the entrypoint restarts the server once after initdb
until psql -q -d "$DATABASE_URL" -c 'select 1' >/dev/null 2>&1; do sleep 0.5; done
psql -q -v ON_ERROR_STOP=1 -d "$DATABASE_URL" -f "$SPIKE/sql/01_graph.sql" -f "$SPIKE/sql/02_outbox.sql" 2>&1 | grep -v -e NOTICE -e cascades || true

pkill -f "python.* $SPIKE/bridge.py" || true
pkill -x rulego-server || true
for i in $(seq 1 50); do curl -s -o /dev/null http://127.0.0.1:9090/ || break; sleep 0.2; done  # port free
rm -rf "$S" && mkdir -p "$S/data"
[ -x "$S/../rulego-server" ] || (cd "$SPIKE/rulego" && GOTOOLCHAIN=auto go build -o "$S/../rulego-server" .)
sed "s#__DATA__#$S/data#" "$SPIKE/rulego/config.conf" > "$S/config.conf"
nohup "$S/../rulego-server" -c="$S/config.conf" > "$S/rulego.log" 2>&1 &
for i in $(seq 1 50); do curl -sf -o /dev/null http://127.0.0.1:9090/api/v1/rules && break; sleep 0.3; done
curl -sf -o /dev/null http://127.0.0.1:9090/api/v1/rules || { tail "$S/rulego.log"; exit 1; }
sed "s#__SPIKE__#$SPIKE#" "$SPIKE/chains/s0_on_change.json" |
  curl -sf -X POST -H 'Content-Type: application/json' --data-binary @- \
    http://127.0.0.1:9090/api/v1/rules/s0_on_change >/dev/null
echo "s0 up: db 127.0.0.1:55432, rulego :9090, state $S"

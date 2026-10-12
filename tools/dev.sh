#!/bin/sh
# Development services: the database container and RuleGo-Server on the host.
#
#   tools/dev.sh up     build or pull what's missing, (re)start both, print the env to use
#   tools/dev.sh down   stop both
#
#   KERNEL_RULEGO_AI=1 tools/dev.sh up   RuleGo built with its AI components (-tags with_ai,
#                                        rulego/components_ai.go), a separate binary
#
# The database is a throwaway container (kernel-db, port $KERNEL_DB_PORT, trust auth, no
# volume). RuleGo runs on the host so x/python steps use the repo venv; its environment
# (DATABASE_URL, KERNEL_GRAPH, PYTHONPATH) is what chain steps see. Nothing here touches the
# graph: the core is installed from Python (kernel.db.install_core).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
STATE=${KERNEL_STATE:-$REPO/data}
PORT=${KERNEL_DB_PORT:-55432}
GRAPH=${KERNEL_GRAPH:-entity}
DATABASE_URL=${DATABASE_URL:-postgresql://agens@127.0.0.1:$PORT/postgres}
RULEGO_BIN=$STATE/bin/rulego-server
TAGS=
if [ "${KERNEL_RULEGO_AI:-0}" = 1 ]; then RULEGO_BIN=$STATE/bin/rulego-ai; TAGS=with_ai; fi

rulego_stop() {
  pkill -x rulego-server || true
  pkill -x rulego-ai || true
  for _ in $(seq 1 50); do curl -s -o /dev/null http://127.0.0.1:9090/ || return 0; sleep 0.2; done
  echo "port 9090 still busy" >&2; return 1
}

case "${1:-}" in
up)
  docker info >/dev/null 2>&1 || { echo "dockerd is not running (cloud session: nohup dockerd >/tmp/dockerd.log 2>&1 &)" >&2; exit 1; }
  docker image inspect kernel-agensgraph >/dev/null 2>&1 || "$REPO/db/agensgraph/build.sh"
  docker rm -f kernel-db >/dev/null 2>&1 || true
  docker run -d --name kernel-db -p "$PORT:5432" -e POSTGRES_HOST_AUTH_METHOD=trust \
    -e POSTGRES_PASSWORD=unused kernel-agensgraph >/dev/null
  sleep 1
  for _ in $(seq 1 60); do psql -q -d "$DATABASE_URL" -c 'select 1' >/dev/null 2>&1 && break; sleep 0.5; done
  psql -q -d "$DATABASE_URL" -c 'select 1' >/dev/null

  # the sandbox's images (config/kernel.yaml, sandbox.images): runs never pull
  for image in $(PYTHONPATH=$REPO "$REPO/.venv/bin/python" -c \
      "from kernel import config; print(' '.join(config.load()['sandbox']['images'].values()))"); do
    docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image" >/dev/null
  done

  rulego_stop
  mkdir -p "$STATE/rulego" "$STATE/bin"
  [ -x "$RULEGO_BIN" ] || (cd "$REPO/rulego" && GOTOOLCHAIN=auto go build -tags "$TAGS" -o "$RULEGO_BIN" .)
  sed "s#__DATA__#$STATE/rulego#" "$REPO/rulego/config.conf" > "$STATE/rulego.conf"
  DATABASE_URL=$DATABASE_URL KERNEL_GRAPH=$GRAPH PYTHONPATH=$REPO PATH="$REPO/.venv/bin:$PATH" \
    nohup "$RULEGO_BIN" -c="$STATE/rulego.conf" > "$STATE/rulego.log" 2>&1 &
  for _ in $(seq 1 50); do curl -sf -o /dev/null http://127.0.0.1:9090/api/v1/rules && break; sleep 0.3; done
  curl -sf -o /dev/null http://127.0.0.1:9090/api/v1/rules || { tail "$STATE/rulego.log"; exit 1; }
  echo "export DATABASE_URL=$DATABASE_URL KERNEL_GRAPH=$GRAPH RULEGO_URL=http://127.0.0.1:9090"
  ;;
down)
  rulego_stop
  docker rm -f kernel-db >/dev/null 2>&1 || true
  ;;
*)
  echo "usage: $0 up|down" >&2; exit 2
  ;;
esac

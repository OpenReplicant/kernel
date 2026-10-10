#!/bin/bash
# Throwaway Postgres 16 for development and tests where no service is running (e.g. a cloud
# container with the binaries installed). Socket only, port 5499, trust auth. Run as root.
#   tools/dev_postgres.sh start   # prints the DATABASE_URL to export
#   tools/dev_postgres.sh stop
set -euo pipefail
D=${PG_DEV_DIR:-/var/lib/postgresql/kscratch}
B=${PG_BIN:-/usr/lib/postgresql/16/bin}
case "${1:-start}" in
  start)
    su postgres -c "rm -rf $D; mkdir -p $D && $B/initdb -D $D/d -A trust >/dev/null && \
      $B/pg_ctl -D $D/d -o '-k $D -p 5499 -c listen_addresses=' -l $D/log start >/dev/null"
    chmod 755 "$D"
    echo "postgresql://postgres@/postgres?host=$D&port=5499" ;;
  stop)
    su postgres -c "$B/pg_ctl -D $D/d stop -m fast >/dev/null; rm -rf $D" ;;
esac

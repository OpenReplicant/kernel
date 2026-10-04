#!/usr/bin/env bash
# First start only: apply the kernel SQL, then create the gateway's login roles (writer:
# the three write functions; reader: select only). Packs are installed afterwards by the
# stack's `packs` service (kernel/packs.py), so the database image holds no pack.
set -euo pipefail

psql_db=(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --quiet)

for f in /opt/wmk/kernel/sql/*.sql; do
  echo "wmk: applying ${f#/opt/wmk/}"
  "${psql_db[@]}" -f "$f"
done

"${psql_db[@]}" \
  -v writer_password="${WMK_WRITER_PASSWORD:?WMK_WRITER_PASSWORD is required}" \
  -v reader_password="${WMK_READER_PASSWORD:?WMK_READER_PASSWORD is required}" <<'SQL'
CREATE ROLE wmk_writer LOGIN PASSWORD :'writer_password' IN ROLE kernel_writer;
CREATE ROLE wmk_reader LOGIN PASSWORD :'reader_password' IN ROLE kernel_reader;
SQL
echo "wmk: kernel ready"

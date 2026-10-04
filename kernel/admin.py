"""Deploy-time helpers: create a database and apply the kernel SQL (and pack SQL) to it.

Used by tests, evals and the replay check. Runs as a superuser (the admin DSN), the
way db/initdb applies the same files when the container first starts.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
KERNEL_SQL = ROOT / "kernel" / "sql"
PACKS = ROOT / "packs"


def sql_files(include_packs: bool = True) -> list[Path]:
    files = sorted(KERNEL_SQL.glob("*.sql"))
    if include_packs:
        for pack in sorted(p for p in PACKS.iterdir() if p.is_dir()):
            files.extend(sorted((pack / "sql").glob("*.sql")))
    return files


def admin_dsn() -> str:
    return os.environ.get("WMK_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")


def dsn_for(base_dsn: str, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(base_dsn, dbname=dbname)


def create_database(name: str, *, base_dsn: str | None = None, drop: bool = True) -> None:
    with psycopg.connect(base_dsn or admin_dsn(), autocommit=True) as conn:
        if drop:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def drop_database(name: str, *, base_dsn: str | None = None) -> None:
    with psycopg.connect(base_dsn or admin_dsn(), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def apply(dbname: str, *, base_dsn: str | None = None, include_packs: bool = True) -> None:
    """Apply every SQL file in order, each in its own transaction."""
    with psycopg.connect(dsn_for(base_dsn or admin_dsn(), dbname)) as conn:
        for path in sql_files(include_packs):
            conn.execute(path.read_text())
            conn.commit()


def ensure_login_roles(writer_password: str, reader_password: str, *, base_dsn: str | None = None) -> None:
    """Create the gateway's login roles if missing: wmk_writer (kernel_writer), wmk_reader (kernel_reader).

    Existing roles are left as they are, so a running gateway keeps its credentials.
    """
    with psycopg.connect(base_dsn or admin_dsn(), autocommit=True) as conn:
        for login, group, password in (
            ("wmk_writer", "kernel_writer", writer_password),
            ("wmk_reader", "kernel_reader", reader_password),
        ):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [login]).fetchone():
                continue
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                    sql.Identifier(login), sql.Literal(password), sql.Identifier(group)
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a kernel database and apply kernel/sql and packs/*/sql."
    )
    parser.add_argument("dbname")
    parser.add_argument("--no-packs", action="store_true")
    parser.add_argument(
        "--keep", action="store_true", help="apply to an existing database instead of recreating"
    )
    args = parser.parse_args()
    if not args.keep:
        create_database(args.dbname)
    apply(args.dbname, include_packs=not args.no_packs)


if __name__ == "__main__":
    main()

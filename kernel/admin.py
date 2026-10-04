"""Deploy-time helpers: create a database, apply the kernel SQL and install packs.

Used by tests, evals and the replay check. Runs as a superuser (the admin DSN), the
way db/initdb applies the same files when the container first starts; packs are then
installed through kernel/packs.py, as the stack's installer service does.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from kernel import packs as pack_installer

ROOT = Path(__file__).resolve().parent.parent
KERNEL_SQL = ROOT / "kernel" / "sql"


def sql_files() -> list[Path]:
    return sorted(KERNEL_SQL.glob("*.sql"))


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


def apply(
    dbname: str, *, base_dsn: str | None = None, packs: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Apply every kernel SQL file in order, each in its own session and transaction, as
    db/initdb does (a file must not depend on session state left by an earlier one), then
    install `packs` (names or folders; None means $WMK_PACKS, else every pack in packs/).
    Returns the installer's result per pack."""
    for path in sql_files():
        with psycopg.connect(dsn_for(base_dsn or admin_dsn(), dbname)) as conn:
            conn.execute(path.read_text())
    return install_packs(dbname, packs, base_dsn=base_dsn)


def install_packs(
    dbname: str, packs: Sequence[str] | None = None, *, base_dsn: str | None = None
) -> list[dict[str, Any]]:
    """Install packs into an existing kernel database; see kernel/packs.py."""
    chosen = pack_installer.resolve(list(packs) if packs is not None else None)
    with psycopg.connect(dsn_for(base_dsn or admin_dsn(), dbname)) as conn:
        return [pack_installer.install(conn, pack) for pack in chosen]


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
        description="Create a kernel database, apply kernel/sql and install packs."
    )
    parser.add_argument("dbname")
    parser.add_argument("--packs", nargs="*", help="packs to install (default: $WMK_PACKS, else all)")
    parser.add_argument(
        "--keep", action="store_true", help="apply to an existing database instead of recreating"
    )
    args = parser.parse_args()
    if not args.keep:
        create_database(args.dbname)
    for result in apply(args.dbname, packs=args.packs):
        print(result)


if __name__ == "__main__":
    main()

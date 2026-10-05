"""Erasure for operators (ADR 0022): see what erasing a person would do, carry it out, and
compact the tables that held their data in the clear.

    python -m kernel.erase scope agt_... [--database wmk]
    python -m kernel.erase run agt_... --requested-by REF --approved-by WHO [--source src_...]
                                       [--reason TEXT] [--yes]

Connects with the admin DSN and acts as kernel_eraser, the one role that may call
kernel.erase. No agent erases: the gateway has no tool for it. Without --yes, `run` prints
the scope and stops. After the keys are destroyed, VACUUM FULL rewrites the key table, the
nodes and the graph's vertex tables, so neither the keys nor the old versions of the
re-projected rows stay in their pages. Copies outside the database (WAL archives, backups,
exports) keep them until they expire.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from kernel import admin

# Tables whose rows erasure deletes or rewrites.
COMPACT = ("kernel.data_keys", "kernel.nodes", 'world."Agent"', 'world."Claim"')


def call(dsn: str, query: str, params: list[Any]) -> Any:
    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE kernel_eraser")
        row = conn.execute(query, params).fetchone()
        assert row is not None
        return row[0]


def compact(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in COMPACT:
            conn.execute(f"VACUUM FULL {table}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and carry out the erasure of a data subject.")
    parser.add_argument("--database", default="wmk")
    sub = parser.add_subparsers(dest="command", required=True)
    scope = sub.add_parser("scope", help="what erasing the subject would destroy and what it would leave")
    scope.add_argument("subject", help="the person's agent id")
    run = sub.add_parser("run", help="destroy the subject's keys and record the erasure")
    run.add_argument("subject", help="the person's agent id")
    run.add_argument("--requested-by", required=True, help="the request: a ticket or reference, not a name")
    run.add_argument("--approved-by", required=True, help="who approved it")
    run.add_argument("--source", action="append", default=[], help="a further sealed source to erase")
    run.add_argument("--reason")
    run.add_argument("--yes", action="store_true", help="erase; without it, show the scope and stop")
    args = parser.parse_args()

    dsn = admin.dsn_for(admin.admin_dsn(), args.database)
    try:
        if args.command == "scope" or not args.yes:
            print(json.dumps(call(dsn, "SELECT kernel.erasure_scope(%s)", [args.subject]), indent=2))
            if args.command == "run":
                print("Nothing erased: run again with --yes to destroy these keys.", file=sys.stderr)
                sys.exit(1)
            return
        request = {
            "subject": args.subject,
            "requested_by": args.requested_by,
            "approved_by": args.approved_by,
            "sources": args.source,
        }
        if args.reason:
            request["reason"] = args.reason
        result = call(dsn, "SELECT kernel.erase(%s)", [Jsonb(request)])
    except psycopg.Error as exc:
        detail = exc.diag.message_detail if exc.sqlstate == "WMK01" else None
        print(detail or f"{type(exc).__name__}: {exc.diag.message_primary}", file=sys.stderr)
        sys.exit(1)
    compact(dsn)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

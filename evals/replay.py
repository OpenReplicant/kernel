"""The replay test: rebuild the graph from the log and diff it against the live graph.

A scratch database gets the kernel SQL, the pack manifests the live database recorded
(in install order), a copy of the log, sources, chunks, data keys and erasure ledger, and
then kernel.rebuild(), which projects every entry in offset order: what a destroyed key
sealed projects as erased (ADR 0022). The ontology, claims, assertions, nodes, edges,
conflicts, touches, redactions and the AGE graph must come out identical. A non-empty diff
exits with status 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import psycopg

from kernel import admin, packs

# Tables copied from the live database: the log, what it cites, and the keys that remain.
SOURCE_OF_TRUTH = ("kernel.sources", "kernel.chunks", "kernel.data_keys", "kernel.erasures", "kernel.log")

# Everything projected from the log, as canonical rows.
SNAPSHOTS: dict[str, str] = {
    "packs": "SELECT jsonb_build_object('name', name, 'version', version, 'hash', manifest_hash) "
    "FROM kernel.packs",
    "ontology": "SELECT to_jsonb(x) FROM kernel.namespaces x "
    "UNION ALL SELECT to_jsonb(x) FROM kernel.kinds x "
    "UNION ALL SELECT to_jsonb(x) FROM kernel.edge_kinds x "
    "UNION ALL SELECT to_jsonb(x) FROM kernel.rules x",
    "claims": "SELECT to_jsonb(x) FROM kernel.claims x ORDER BY id",
    "assertions": "SELECT to_jsonb(x) FROM kernel.assertions x ORDER BY id",
    "nodes": "SELECT to_jsonb(x) - 'embedding' || jsonb_build_object('embedding', x.embedding::text) "
    "FROM kernel.nodes x ORDER BY id",
    "edges": "SELECT to_jsonb(x) FROM kernel.edges x ORDER BY id",
    "conflicts": "SELECT to_jsonb(x) FROM kernel.conflicts x ORDER BY edge_a, edge_b, rule",
    "node_touches": "SELECT to_jsonb(x) FROM kernel.node_touches x ORDER BY node_id, log_offset",
    "claim_redactions": "SELECT to_jsonb(x) FROM kernel.claim_redactions x ORDER BY claim_id",
    "graph_vertices": "SELECT jsonb_build_object('label', v.tableoid::regclass::text, 'properties', "
    "v.properties::text::jsonb) FROM world._ag_label_vertex v",
    "graph_edges": "SELECT jsonb_build_object('label', e.tableoid::regclass::text, "
    "'from', s.properties::text::jsonb ->> 'id', 'to', t.properties::text::jsonb ->> 'id', "
    "'properties', e.properties::text::jsonb) "
    "FROM world._ag_label_edge e JOIN world._ag_label_vertex s ON s.id = e.start_id "
    "JOIN world._ag_label_vertex t ON t.id = e.end_id",
}


def snapshot(dsn: str) -> dict[str, list[str]]:
    with psycopg.connect(dsn) as conn:
        conn.execute("SET search_path = ag_catalog, kernel, public")
        return {
            name: sorted(json.dumps(row[0], sort_keys=True) for row in conn.execute(query).fetchall())
            for name, query in SNAPSHOTS.items()
        }


def diff(live: dict[str, list[str]], rebuilt: dict[str, list[str]], limit: int = 20) -> list[str]:
    lines: list[str] = []
    for name in SNAPSHOTS:
        a, b = set(live[name]), set(rebuilt[name])
        only_live, only_rebuilt = sorted(a - b), sorted(b - a)
        if len(live[name]) != len(rebuilt[name]):
            lines.append(f"{name}: {len(live[name])} live rows, {len(rebuilt[name])} rebuilt rows")
        lines += [f"{name} - live:    {row}" for row in only_live[:limit]]
        lines += [f"{name} + rebuilt: {row}" for row in only_rebuilt[:limit]]
    return lines


def replay(database: str, *, base_dsn: str | None = None, keep: bool = False) -> tuple[int, list[str]]:
    """Rebuild `database`'s projections in a scratch database; return (entries replayed, diff lines)."""
    base = base_dsn or admin.admin_dsn()
    scratch = f"{database}_replay"
    live_dsn = admin.dsn_for(base, database)
    scratch_dsn = admin.dsn_for(base, scratch)
    admin.create_database(scratch, base_dsn=base)
    try:
        admin.apply(scratch, base_dsn=base, packs=[])
        with psycopg.connect(live_dsn) as src, psycopg.connect(scratch_dsn) as dst:
            for (manifest,) in src.execute("SELECT manifest FROM kernel.packs ORDER BY install_seq"):
                packs.install_manifest(dst, manifest)
            for table in SOURCE_OF_TRUTH:
                with (
                    src.cursor().copy(f"COPY {table} TO STDOUT (FORMAT BINARY)") as out,
                    dst.cursor().copy(f"COPY {table} FROM STDIN (FORMAT BINARY)") as into,
                ):
                    for data in out:
                        into.write(data)
            dst.commit()
            entries: Any = dst.execute("SELECT kernel.rebuild()").fetchone()
            dst.commit()
        return int(entries[0]), diff(snapshot(live_dsn), snapshot(scratch_dsn))
    finally:
        if not keep:
            admin.drop_database(scratch, base_dsn=base)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild the graph from the log and diff against the live graph."
    )
    parser.add_argument("--database", default="wmk")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database for inspection")
    args = parser.parse_args()
    entries, lines = replay(args.database, keep=args.keep)
    if lines:
        print(f"replay of {args.database}: {entries} entries, DIFF ({len(lines)} lines)")
        print("\n".join(lines))
        sys.exit(1)
    print(f"replay of {args.database}: {entries} entries, graph reproduced exactly")


if __name__ == "__main__":
    main()

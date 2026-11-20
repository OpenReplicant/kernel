"""encoder-substrate: Substrate's store into the kernel, in Wikidata's and Substrate's own terms (D-103). A plain command.

  python -m encoder_substrate [--store DSN] [--scope IRI] [--dry-run]

Reads the store (schema `store`, read-only session) and submits one change set per item (encode.py), keyed by the
item and the content of what it says, so running again over an unchanged store submits nothing. When an item
changed, its change set also deprecates what this encoder said about it before: a claim the new version states
again is superseded by it, any other is retracted; nothing is deleted. Findings (a property with no term, a
somevalue, a statement on a property, an uncited statement) are printed and kept with the run.

Output: one JSON line, the run's summary.

Env: SUBSTRATE_STORE_URL (or --store; a libpq connection string for the store's database), DATABASE_URL (the
module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default kkm_encoder_substrate).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

from kk.lib import connect, run, submit

from .encode import AGENT, SCOPE, change_set, content_of, encode_store, key_prefix, supersede

MODULE = "urn:kk:module:encoder-substrate"
PRIOR = """select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.status
           from api.claims c join api.change_sets s on s.id = c.change_set
           where c.attributed_to = %s and c.status <> 'deprecated'
             and starts_with(s.idempotency_key, %s) and s.idempotency_key <> %s"""
# The item's latest change set from this encoder that is in force or on its way (an invalid or rejected one
# changed nothing): "unchanged" means the same content as that one, not as any earlier one, so an item that
# returns to what it said before is submitted again and supersedes what came between.
LATEST = """select id, idempotency_key from api.change_sets
            where attributed_to = %s and starts_with(idempotency_key, %s)
              and status not in ('invalid', 'rejected', 'conflict')
            order by submitted_at desc, id desc limit 1"""


def rows(cur, query: str) -> list[dict]:
    cur.execute(query)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_store(dsn: str) -> tuple[list[dict], list[dict]]:
    """Entities and statements, with their qualifiers and references; read-only."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            entities = rows(cur, "select id, kind, datatype, key, label, description, aliases, prose from store.entity")
            statements = rows(cur, "select id::text, subject, property, snak, value, value_entity, rank from store.statement")
            quals: dict[str, list] = {}
            for q in rows(cur, "select statement_id::text as sid, ord, property, snak, value, value_entity from store.qualifier"):
                quals.setdefault(q.pop("sid"), []).append(q)
            refs: dict[str, list] = {}
            for r in rows(cur, """
                select c.statement_id::text as sid, r.id::text as id, r.by_kind, r.by_name, r.by_version, r.run,
                       sp.locator, sp.text, so.uri as source
                from store.cites c join store.reference r on r.id = c.reference_id
                left join store.span sp on sp.id = r.span_id left join store.source so on so.id = sp.source_id
                order by r.id"""):
                sid = r.pop("sid")
                loc, text, source = r.pop("locator"), r.pop("text"), r.pop("source")
                r["span"] = {"locator": loc, "text": text, "source": source} if loc else None
                refs.setdefault(sid, []).append(r)
    for st in statements:
        st["qualifiers"], st["references"] = quals.get(st["id"], []), refs.get(st["id"], [])
    return entities, statements


def export(cur, entities, statements, scope: str, dry_run: bool = False) -> dict:
    sets, findings, counts = encode_store(entities, statements)
    todo = []
    for item_id, _iri, ops in sets:
        cs = change_set(item_id, ops, scope)
        cur.execute(LATEST, (AGENT, key_prefix(item_id)))
        latest = cur.fetchone()
        if latest and content_of(latest[1]) == content_of(cs["idempotency_key"]):
            counts["unchanged"] += 1
            continue
        if latest:   # new content, or content said before and replaced since: key it after the latest
            cs["idempotency_key"] += f":after:{str(latest[0])[:8]}"
        cur.execute(PRIOR, (AGENT, key_prefix(item_id), cs["idempotency_key"]))
        cols = [d.name for d in cur.description]
        prior = [dict(zip(cols, r)) for r in cur.fetchall()]
        if prior:
            supersede(cs, prior)
        todo.append((item_id, cs, len(prior)))
    summary = {"counts": counts, "findings": findings, "new": len(todo), "deprecated": sum(p for _, _, p in todo),
               "change_sets": []}
    if dry_run or not todo:   # nothing to submit: no run either
        return summary
    with run(cur, MODULE, {"scope": scope}) as (run_id, outputs):
        for item_id, cs, deprecated in todo:
            res = submit(cur, cs, run_id)
            summary["change_sets"].append({"item": item_id, "id": str(res["id"]), "status": res["status"],
                                           "deprecated": deprecated})
        outputs.update(counts=counts, findings=findings, new=len(todo), deprecated=summary["deprecated"])
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record Substrate's store as claims, in Wikidata's and Substrate's terms.")
    ap.add_argument("--store", default=os.environ.get("SUBSTRATE_STORE_URL"),
                    help="the store's database, a libpq connection string (default: SUBSTRATE_STORE_URL)")
    ap.add_argument("--scope", default=SCOPE, help=f"target scope (default: {SCOPE})")
    ap.add_argument("--dry-run", action="store_true", help="say what would be submitted, submit nothing")
    a = ap.parse_args(argv)
    if not a.store:
        ap.error("the store's connection string: --store or SUBSTRATE_STORE_URL")
    entities, statements = read_store(a.store)
    with connect(os.environ.get("KK_ROLE", "kkm_encoder_substrate")) as conn, conn.cursor() as cur:
        print(json.dumps(export(cur, entities, statements, a.scope, a.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

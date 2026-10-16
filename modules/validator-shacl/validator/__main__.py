"""Validator worker: validates submitted change sets and records each verdict. Needs no runner.

It listens on the kernel_change_set channel and, because NOTIFY is not durable, also sweeps the
queue on start and every KK_SWEEP_SECONDS. Each submitted change set is judged by validator.core
and the verdict recorded with api.record_validation; the kernel applies valid auto-lane sets.

Runs inside `kk serve`, or alone: python -m validator (needs the kk package for kk.lib).
Env: DATABASE_URL (a login that may SET ROLE to KK_ROLE, e.g. kk_authenticator), KK_ROLE (default
kkm_validator), KK_SWEEP_SECONDS (default 30).
"""
import os
import sys

from psycopg.types.json import Jsonb

from kk.lib import connect, listen

from .core import entity_objects, validate

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
QUEUE = """select id, idempotency_key, attributed_to, generated_by, target_scope, lane, ops
           from api.validation_queue order by submitted_at limit 50"""


def context(cur, cs: dict) -> list:
    """rdf:type claims (not deprecated) of the entities the change set points at."""
    iris = sorted(entity_objects(cs))
    if not iris:
        return []
    cur.execute("select subject, object_entity from api.claims where predicate = %s and subject = any(%s)"
                " and status <> 'deprecated' and object_entity is not null", (RDF_TYPE, iris))
    return [(s, RDF_TYPE, o) for s, o in cur.fetchall()]


def drain(conn) -> int:
    """Validate everything in the queue; returns how many change sets were judged."""
    judged = 0
    with conn.cursor() as cur:
        while True:
            cur.execute(QUEUE)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if not rows:
                return judged
            for cs in rows:
                cs = {k: (str(v) if k in ("id", "generated_by") and v is not None else v) for k, v in cs.items()}
                try:
                    ok, report = validate(cs, context(cur, cs))
                except Exception as e:  # a crash on one change set must not stop the queue
                    ok, report = False, {"error": f"{type(e).__name__}: {e}"}
                cur.execute("select api.record_validation(%s, %s, %s)", (cs["id"], ok, Jsonb(report)))
                print(f"{cs['id']} {cs['idempotency_key']}: {'valid' if ok else 'invalid'}", flush=True)
                judged += 1


def main() -> None:
    with connect(os.environ.get("KK_ROLE", "kkm_validator")) as conn:
        print("validator: listening on kernel_change_set", flush=True)
        listen(conn, drain)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

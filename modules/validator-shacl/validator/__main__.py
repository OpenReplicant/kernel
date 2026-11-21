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

import psycopg

from psycopg.types.json import Jsonb

from kk.lib import connect, listen

from .core import entity_objects, validate

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
QUEUE = """select id, idempotency_key, attributed_to, generated_by, target_scope, lane, ops
           from api.validation_queue where not (id = any(%s::uuid[])) order by submitted_at limit 50"""
# How long an apply may wait on a row another transaction holds before it gives up and is tried again later
# (D-111): without it, an agent holding an uncommitted entity insert could keep the apply lock taken for good.
LOCK_TIMEOUT = os.environ.get("KK_LOCK_TIMEOUT", "5s")


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
    judged, later = 0, []                          # later: not recorded this pass (a transient error)
    with conn.cursor() as cur:
        while True:
            cur.execute(QUEUE, (later,))
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
                try:
                    cur.execute("select api.record_validation(%s, %s, %s)", (cs["id"], ok, Jsonb(report)))
                except psycopg.errors.OperationalError as e:
                    if conn.closed:
                        raise
                    # D-111 (O-9): a deadlock, timeout or lack of resources during apply is not a verdict. The
                    # change set stays submitted; the pass goes on with the rest and the next wake or sweep tries
                    # it again, so one that keeps failing holds up nothing behind it.
                    later.append(cs["id"])
                    print(f"{cs['id']} {cs['idempotency_key']}: not recorded ({e.sqlstate}), tried again later", flush=True)
                    continue
                print(f"{cs['id']} {cs['idempotency_key']}: {'valid' if ok else 'invalid'}", flush=True)
                judged += 1


def main() -> None:
    with connect(os.environ.get("KK_ROLE", "kkm_validator")) as conn:
        conn.execute("select set_config('lock_timeout', %s, false)", (LOCK_TIMEOUT,))
        print("validator: listening on kernel_change_set", flush=True)
        listen(conn, drain)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

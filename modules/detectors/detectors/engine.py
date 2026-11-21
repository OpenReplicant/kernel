"""The detector engine: from a cursor over `api.applied`, run each detector on its owner's connection,
compare with the matches stored as claims and record formed and dissolved events (D-110).

  engine = Engine(detectors, role="kkm_detectors", agent="urn:kk:agent:detectors", module="urn:kk:module:detectors")
  engine.run_pass(conn)            # one pass over every detector; conn = the module's own connection

Nothing here writes a table: the engine reads `api` views (and `kernel.claim_status`, read only, for the
claims whose status a change set changed) and writes by `submit_change_set`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from kk.lib import connect, run, submit

from .core import (DCT_TYPE, DISSOLVED, FORMED, INSTRUMENT, RDF_TYPE, RELATION, LABEL, USED, Match, Stored,
                   change_set_key, detector_iri, diff, event_ops, match_iri)

DEFAULT_SCOPE = "urn:kk:scope:default"
LOCK_KEY = "select pg_try_advisory_lock(hashtext(%s))"


@dataclass
class Detector:
    """One detector. `sql` is a query returning `key`, `nodes` (text[]), `claims` (uuid[]) and optionally
    `data` (jsonb), with the parameter %(anchors)s (text[], or null for a full pass); or `find(conn, anchors)`
    returning Matches. `anchored=False`: its matches can change when something it does not name changes (a
    count over everything), so it always runs in full and any stored match can dissolve. `react` is called
    with the module's connection, the formed matches, the dissolved ones and the run id, before the events
    are recorded: it must be idempotent, as a pass that failed is repeated."""
    name: str
    owner: str
    sql: Optional[str] = None
    find: Optional[Callable] = None
    react: Optional[Callable] = None
    scope: str = DEFAULT_SCOPE
    anchored: bool = True
    prefixes: list = field(default_factory=list)   # idempotency-key prefixes of the change sets react submits


def _matches(rows, cols) -> list[Match]:
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append(Match(d["key"], tuple(d["nodes"] or ()), tuple(str(c) for c in d["claims"] or ()), d.get("data") or {}))
    return out


class Engine:
    def __init__(self, detectors: list[Detector], role: str, agent: str, module: str,
                 full_every: Optional[int] = None, batch: Optional[int] = None, checkpoint: Optional[int] = None,
                 timeout_ms: Optional[int] = None):
        self.detectors, self.role, self.agent, self.module = detectors, role, agent, module
        self.full_every = full_every or int(os.environ.get("KK_DETECTORS_FULL_EVERY", "20"))
        self.batch = batch or int(os.environ.get("KK_DETECTORS_BATCH", "1000"))
        self.checkpoint = checkpoint or int(os.environ.get("KK_DETECTORS_CHECKPOINT", "1000"))
        self.timeout_ms = timeout_ms or int(os.environ.get("KK_DETECTORS_TIMEOUT_MS", "10000"))
        self.cursors: dict[str, int] = {}      # in this process; the stored one is read from the runs after a restart
        self.saved: dict[str, int] = {}
        self.passes: dict[str, int] = {}
        self.owners: dict[str, object] = {}

    # ------------------------------------------------------------ one pass
    def run_pass(self, conn) -> dict:
        """Every detector once, one at a time. A detector that raises is recorded as a failed run and the
        others go on. Two passes never overlap (an advisory lock, tried, never waited for)."""
        if not conn.execute(LOCK_KEY, (self.agent,)).fetchone()[0]:
            return {"status": "busy"}
        try:
            result = {}
            for d in self.detectors:
                try:
                    result[d.name] = self.step(conn, d)
                except Exception as e:     # this detector stops for this pass, the others do not
                    if not getattr(e, "recorded", False):
                        self.fail(conn, d, e)
                    result[d.name] = {"status": "failed", "error": str(e)[:300]}
            return result
        finally:
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (self.agent,))

    def owner_conn(self, conn, role: str):
        if role == self.role:
            return conn
        if role not in self.owners or self.owners[role].closed:
            self.owners[role] = connect(role)
        return self.owners[role]

    def in_flight(self, conn, d: Detector) -> bool:
        prefixes = [f"detectors:{d.name}|", *d.prefixes]
        return conn.execute(
            "select exists (select 1 from api.change_sets s where s.attributed_to = %s "
            "and s.status not in ('applied','invalid','rejected','conflict') "
            "and exists (select 1 from unnest(%s::text[]) p where starts_with(s.idempotency_key, p)))",
            (self.agent, prefixes)).fetchone()[0]

    def cursor(self, conn, name: str) -> Optional[int]:
        if name not in self.cursors:
            row = conn.execute(
                "select max((info->>'cursor')::bigint) from kernel.run where agent = %s and status = 'succeeded' "
                "and info->>'detector' = %s", (self.agent, name)).fetchone()
            if row[0] is not None:
                self.cursors[name] = self.saved[name] = row[0]
        return self.cursors.get(name)

    def step(self, conn, d: Detector) -> dict:
        if self.in_flight(conn, d):      # our last change set is not applied yet: its matches are not stored yet
            return {"status": "waiting"}
        owner = self.owner_conn(conn, d.owner)
        cursor = self.cursor(conn, d.name)
        n = self.passes[d.name] = self.passes.get(d.name, 0) + 1
        top = owner.execute("select coalesce(max(seq), 0) from api.applied").fetchone()[0]
        periodic = d.anchored and n % self.full_every == 0       # a full look now and then: redaction has no applied row
        if cursor is not None and top <= cursor and not periodic:
            return {"status": "idle"}                            # nothing applied since the last look
        if cursor is None or not d.anchored or periodic:
            to, anchors = max(top, cursor or 0), None
        else:
            rows = owner.execute("select seq, change_set from api.applied where seq > %s order by seq limit %s",
                                 (cursor, self.batch)).fetchall()
            to = rows[-1][0]
            anchors = changed(owner, [r[1] for r in rows])
        current = [] if anchors == [] else self.find(owner, d, anchors)
        stored = self.stored(conn, d)
        formed, dissolved = diff(current, stored, anchors)
        quiet = not formed and not dissolved
        if quiet:
            self.cursors[d.name] = to
            if to - self.saved.get(d.name, 0) < self.checkpoint:
                return {"status": "ok", "cursor": to, "formed": 0, "dissolved": 0}
        known = self.known(conn, d, formed)
        now = datetime.now(timezone.utc).isoformat()
        with conn.cursor() as cur, run(cur, self.module, {"detector": d.name, "cursor": to, "from": cursor,
                                                           "full": anchors is None}) as (rid, outputs):
            try:
                if d.react:
                    d.react(conn, formed, dissolved, rid)
                ops = event_ops(d.name, formed, dissolved, to, now, known)
                if ops:
                    res = submit(cur, {"idempotency_key": change_set_key(d.name, cursor, to, formed, dissolved),
                                       "attributed_to": self.agent, "target_scope": d.scope, "ops": ops}, rid)
                    if res.get("status") in ("invalid", "rejected", "conflict"):
                        raise RuntimeError(f"the kernel did not take the events: {res}")
            except Exception as e:     # the run records the failure and why
                outputs["error"] = f"{type(e).__name__}: {e}"[:500]
                e.recorded = True
                raise
            outputs.update(formed=len(formed), dissolved=len(dissolved))
        self.cursors[d.name] = self.saved[d.name] = to
        return {"status": "ok", "cursor": to, "formed": len(formed), "dissolved": len(dissolved)}

    def find(self, owner, d: Detector, anchors) -> list[Match]:
        if d.find:
            return list(d.find(owner, anchors))
        with owner.transaction():
            owner.execute("set transaction read only")
            owner.execute(f"set local statement_timeout = {int(self.timeout_ms)}")
            cur = owner.execute(d.sql, {"anchors": anchors})
            return _matches(cur.fetchall(), [c.name for c in cur.description])

    def fail(self, conn, d: Detector, error: Exception) -> None:
        """A failed run, with the error: the record that this detector stopped, and why."""
        try:
            with conn.cursor() as cur:
                cur.execute("select api.start_run(%s, null, '[]', %s)", (self.module, _jsonb({"detector": d.name, "failed": True})))
                rid = cur.fetchone()[0]
                cur.execute("select api.finish_run(%s, 'failed', %s)", (rid, _jsonb({"error": f"{type(error).__name__}: {error}"[:500]})))
        except Exception:
            pass

    # ------------------------------------------------------------ what is stored, as claims
    def stored(self, conn, d: Detector) -> dict[str, Stored]:
        det = detector_iri(d.name)
        rows = conn.execute(
            "select distinct on (u.object_entity) u.object_entity, i.subject, k.object_entity, t.id "
            "from api.claims i "
            "join api.claims u on u.subject = i.subject and u.predicate = %s and u.status <> 'deprecated' "
            "join api.claims k on k.subject = i.subject and k.predicate = %s and k.status <> 'deprecated' "
            "join api.claims t on t.subject = i.subject and t.predicate = %s and t.status <> 'deprecated' "
            "join api.applied a on a.change_set = i.change_set "
            "where i.predicate = %s and i.object_entity = %s and i.status <> 'deprecated' "
            "order by u.object_entity, a.seq desc", (USED, DCT_TYPE, RDF_TYPE, INSTRUMENT, det)).fetchall()
        holding = [r for r in rows if r[2] == FORMED]
        if not holding:
            return {}
        nodes: dict[str, list] = {}
        for s, o in conn.execute("select subject, object_entity from api.claims where subject = any(%s) and predicate = %s "
                                 "and status <> 'deprecated'", ([r[1] for r in holding], RELATION)):
            nodes.setdefault(s, []).append(o)
        keys = dict(conn.execute("select subject, object_value->>'@value' from api.claims where subject = any(%s) "
                                 "and predicate = %s and status <> 'deprecated'", ([r[0] for r in holding], LABEL)))
        return {keys[m]: Stored(keys[m], tuple(sorted(nodes.get(ev, []))), str(t)) for m, ev, _, t in holding if m in keys}

    def known(self, conn, d: Detector, formed: list[Match]) -> set[str]:
        who = [match_iri(d.name, m.key) for m in formed]
        if not who:
            return set()
        return {r[0] for r in conn.execute("select distinct subject from api.claims where subject = any(%s) and predicate = %s "
                                           "and status <> 'deprecated'", (who, LABEL))}


def changed(owner, change_sets: list) -> list[str]:
    """Entities named by the claims these change sets added, or whose status they changed, as the owner reads
    them: the subjects and the entity objects, the anchors a detector restricts its query to."""
    rows = owner.execute(
        "select c.subject, c.object_entity from kernel.claim c where c.change_set = any(%s) "
        "union select c.subject, c.object_entity from kernel.claim_status s join kernel.claim c on c.id = s.claim_id "
        "where s.change_set = any(%s)", (change_sets, change_sets)).fetchall()
    return sorted({x for r in rows for x in r if x})


def _jsonb(v):
    from psycopg.types.json import Jsonb
    return Jsonb(v)

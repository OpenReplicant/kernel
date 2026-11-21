"""The detector engine: from a cursor over `api.applied`, run each detector on its owner's connection,
compare with the matches stored as claims and record formed and dissolved events (D-110).

  engine = Engine(detectors, role="kkm_detectors", agent="urn:kk:agent:detectors", module="urn:kk:module:detectors")
  engine.run_pass(conn)            # one pass over every detector; conn = the module's own connection

Nothing here writes a table: the engine reads `api` views (and `kernel.claim_status`, `kernel.belief` and
`kernel.verification`, read only, for the claims a change set re-ranked or scored, and `kernel.scope_grant`
for the clearance check: none of them has an `api` view) and writes by `submit_change_set`.
"""
from __future__ import annotations

import inspect
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import psycopg
import psycopg.errors
import psycopg.sql

from kk.lib import connect, run, submit

from .core import (DCT_TYPE, DISSOLVED, FORMED, INSTRUMENT, RDF_TYPE, RELATION, LABEL, USED, Match, Stored,
                   change_set_key, chunks, detector_iri, diff, event_ops, match_iri)

DEFAULT_SCOPE = "urn:kk:scope:default"
LOCK_KEY = "select pg_try_advisory_lock(hashtext(%s))"
FORBIDDEN_OWNER = re.compile(r"^(person_|agent_|kk_)")     # people, agents and the kernel's own roles never own a detector
REFUSED = ("invalid", "rejected", "conflict")


class Refused(ValueError):
    """A detector the engine will not run: its owner or scope would leak what the owner can read."""


@dataclass
class Detector:
    """One detector. `sql` is a query returning `key`, `nodes` (text[]), `claims` (uuid[]) and optionally
    `data` (jsonb), with the parameter %(anchors)s (text[], or null for a full pass; a literal % in the query
    is written %%); or `find(conn, anchors)` returning Matches. `anchored=False`: its matches can change when
    something it does not name changes (a count over everything), so it always runs in full and any stored
    match can dissolve. `react` is called with the module's connection, the formed matches, the dissolved ones
    and the run id, before the events are recorded: it must be idempotent, as a pass that failed is repeated.
    It may also take `first` (the first look, no cursor yet) and `current` (every match found now).
    The key, nodes and data of a match are content, written to `scope`: see the clearance rule in README.md."""
    name: str
    owner: str
    sql: Optional[str] = None
    find: Optional[Callable] = None
    react: Optional[Callable] = None
    scope: str = DEFAULT_SCOPE
    anchored: bool = True
    prefixes: list = field(default_factory=list)   # idempotency-key prefixes of the change sets react submits

    def __post_init__(self):
        if FORBIDDEN_OWNER.match(self.owner):
            raise Refused(f"detector {self.name}: {self.owner} cannot own a detector (person_*, agent_* and kk_* roles are refused)")


def _matches(rows, cols) -> list[Match]:
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append(Match(d["key"], tuple(d["nodes"] or ()), tuple(str(c) for c in d["claims"] or ()), d.get("data") or {}))
    return out


class Engine:
    def __init__(self, detectors: list[Detector], role: str, agent: str, module: str,
                 full_every: Optional[int] = None, batch: Optional[int] = None, checkpoint: Optional[int] = None,
                 timeout_ms: Optional[int] = None, chunk: Optional[int] = None, backoff_s: Optional[float] = None):
        names = [d.name for d in detectors]
        if len(set(names)) != len(names):
            raise ValueError("detector names must be unique")
        self.detectors, self.role, self.agent, self.module = detectors, role, agent, module
        self.full_every = full_every or int(os.environ.get("KK_DETECTORS_FULL_EVERY", "20"))
        self.batch = batch or int(os.environ.get("KK_DETECTORS_BATCH", "1000"))
        self.checkpoint = checkpoint or int(os.environ.get("KK_DETECTORS_CHECKPOINT", "1000"))
        self.timeout_ms = timeout_ms or int(os.environ.get("KK_DETECTORS_TIMEOUT_MS", "10000"))
        self.chunk = chunk or int(os.environ.get("KK_DETECTORS_CHUNK", "200"))
        self.backoff_s = float(os.environ.get("KK_DETECTORS_BACKOFF_S", "5")) if backoff_s is None else backoff_s
        self.cursors: dict[str, int] = {}      # in this process; the stored one is read from the runs after a restart
        self.saved: dict[str, int] = {}
        self.passes: dict[str, int] = {}
        self.owners: dict[str, object] = {}    # one connection per owner role, never shared and never the module's own
        self.failures: dict[str, tuple[int, float]] = {}   # consecutive failures and when to try again

    # ------------------------------------------------------------ one pass
    def run_pass(self, conn) -> dict:
        """Every detector once, one at a time. A detector that raises is recorded as a failed run and the
        others go on; one that keeps failing is tried less often. Two passes never overlap (an advisory lock,
        tried, never waited for)."""
        if not conn.execute(LOCK_KEY, (self.agent,)).fetchone()[0]:
            return {"status": "busy"}
        try:
            result = {}
            for d in self.detectors:
                n, until = self.failures.get(d.name, (0, 0.0))
                if n and time.monotonic() < until:
                    result[d.name] = {"status": "backoff", "failures": n}
                    continue
                try:
                    result[d.name] = self.step(conn, d)
                    self.failures.pop(d.name, None)
                except Exception as e:     # this detector stops for this pass, the others do not
                    if not getattr(e, "recorded", False):
                        self.fail(conn, d, e)
                    delay = min(self.backoff_s * 2 ** n, 300.0) if self.backoff_s else 0.0
                    self.failures[d.name] = (n + 1, time.monotonic() + delay)
                    result[d.name] = {"status": "failed", "error": str(e)[:300]}
            return result
        finally:
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (self.agent,))

    # ------------------------------------------------------------ the owner's connection
    def owner_conn(self, role: str):
        """The owner's own connection, scrubbed (discard all: settings, temp objects, session locks) and set to
        the owner's role again. A connection that does not come back as the owner is dropped."""
        c = self.owners.get(role)
        if c is None or c.closed:
            c = self.owners[role] = connect(role)
            c.prepare_threshold = None      # discard all drops prepared statements: psycopg must not cache any
            c.session_login = c.execute("select session_user").fetchone()[0]
        else:
            c.execute("discard all")
            c.execute(psycopg.sql.SQL("set role {}").format(psycopg.sql.Identifier(role)))
        if not self.same(c, role):
            self.drop(role)
            raise Refused(f"the connection of {role} is not {role}")
        return c

    @staticmethod
    def same(c, role: str) -> bool:
        """current_user and session_user are what they were: the detector's query did not switch role."""
        try:
            cur, ses = c.execute("select current_user, session_user").fetchone()
            return cur == role and ses == c.session_login
        except Exception:
            return False

    def drop(self, role: str) -> None:
        c = self.owners.pop(role, None)
        try:
            c and c.close()
        except Exception:
            pass

    def check_clearance(self, conn, d: Detector) -> None:
        """Refuse a detector whose events could carry what some reader of its event scope may not read.

        Its key, nodes and data come from claims its owner reads (also through a negation: `not exists` over a
        scope says something about it). They are written to `d.scope`. So the readers of `d.scope` must be able
        to read every scope the owner can: no reader of the event scope may lack a grant the owner has. This is
        the kernel's readers_subset (target readers within the input's), with a role counting as able to read
        a scope when it is a member of a role granted it (as `can_read` does)."""
        r = conn.execute("select rolsuper or rolbypassrls from pg_roles where rolname = %s", (d.owner,)).fetchone()
        if r is None:
            raise Refused(f"detector {d.name}: owner role {d.owner} does not exist")
        if r[0]:
            raise Refused(f"detector {d.name}: owner role {d.owner} is a superuser or bypasses row-level security")
        wider = [x[0] for x in conn.execute(
            "select distinct x.scope from kernel.scope_grant x "
            "where x.can_read and pg_has_role(%s::name, x.db_role, 'member') and x.scope <> %s "
            "and exists (select 1 from kernel.scope_grant t where t.scope = %s and t.can_read "
            "  and not exists (select 1 from kernel.scope_grant s where s.scope = x.scope and s.can_read "
            "                  and pg_has_role(t.db_role, s.db_role, 'member')))",
            (d.owner, d.scope, d.scope)).fetchall()]
        if wider:
            raise Refused(f"detector {d.name}: owner {d.owner} reads {sorted(wider)}, which some reader of the event scope "
                          f"{d.scope} may not: events would leak it. Write its events to a scope no more readable than those, "
                          f"or use an owner cleared for less")

    def validate(self, conn) -> dict[str, str]:
        """The detectors this engine would refuse now, and why (checked again on every pass)."""
        out = {}
        for d in self.detectors:
            try:
                self.check_clearance(conn, d)
            except Refused as e:
                out[d.name] = str(e)
        return out

    def in_flight(self, conn, d: Detector) -> bool:
        prefixes = [f"detectors:{d.name}|", *d.prefixes]
        return conn.execute(
            "select exists (select 1 from api.change_sets s where s.attributed_to = %s "
            "and s.status not in ('applied','invalid','rejected','conflict') "
            "and exists (select 1 from unnest(%s::text[]) p where starts_with(s.idempotency_key, p)))",
            (self.agent, prefixes)).fetchone()[0]

    def cursor(self, conn, name: str) -> Optional[int]:
        """The highest cursor of a succeeded run whose change sets the kernel did not refuse: the cursor moves
        only once what the run submitted is applied, and a refused one is looked at again."""
        if name not in self.cursors:
            row = conn.execute(
                "select max((r.info->>'cursor')::bigint) from kernel.run r where r.agent = %s and r.status = 'succeeded' "
                "and r.info->>'detector' = %s and not exists "
                "(select 1 from api.change_sets s where s.generated_by = r.id and s.status in ('invalid','rejected','conflict'))",
                (self.agent, name)).fetchone()
            if row[0] is not None:
                self.cursors[name] = self.saved[name] = row[0]
        return self.cursors.get(name)

    def unreported(self, conn, d: Detector):
        """A run of this detector whose change set the kernel refused, not yet reported as a failure."""
        return conn.execute(
            "select r.id, s.status, s.validation_report from kernel.run r join api.change_sets s on s.generated_by = r.id "
            "where r.agent = %s and r.status = 'succeeded' and r.info->>'detector' = %s and s.status in ('invalid','rejected','conflict') "
            "and not exists (select 1 from kernel.run f where f.agent = r.agent and f.info->>'reported' = r.id::text) "
            "order by r.started_at desc limit 1", (self.agent, d.name)).fetchone()

    def step(self, conn, d: Detector) -> dict:
        self.check_clearance(conn, d)
        if self.in_flight(conn, d):      # our last change set is not applied yet: its matches are not stored yet
            return {"status": "waiting"}
        bad = self.unreported(conn, d)
        if bad:
            e = RuntimeError(f"the kernel did not apply the change set of run {bad[0]}: {bad[1]} {str(bad[2])[:200]}")
            self.fail(conn, d, e, reported=str(bad[0]))
            e.recorded = True
            raise e
        owner = self.owner_conn(d.owner)
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
        stored = self.stored(conn, d, anchors, [m.key for m in current])
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
                    self.call_react(d, conn, formed, dissolved, rid, first=cursor is None, current=current)
                for part, (f, dd) in enumerate(chunks(formed, dissolved, self.chunk)):
                    ops = event_ops(d.name, f, dd, to, now, known)
                    key = change_set_key(d.name, cursor, to, f, dd, part, self.refused_before(conn, d.name, cursor, to, f, dd, part))
                    self.submit_own(conn, cur, key, d, ops, rid)
            except Exception as e:     # the run records the failure and why
                outputs["error"] = f"{type(e).__name__}: {e}"[:500]
                e.recorded = True
                raise
            outputs.update(formed=len(formed), dissolved=len(dissolved))
        # the cursor in memory moves only after the change sets are applied: it is read back from the runs then
        self.cursors.pop(d.name, None)
        return {"status": "ok", "cursor": to, "formed": len(formed), "dissolved": len(dissolved)}

    def call_react(self, d: Detector, conn, formed, dissolved, rid, first: bool, current: list) -> None:
        params = inspect.signature(d.react).parameters
        extra = {k: v for k, v in (("first", first), ("current", current)) if k in params}
        d.react(conn, formed, dissolved, rid, **extra)

    def refused_before(self, conn, name, cursor, to, f, dd, part) -> int:
        """How many change sets with this very key the kernel already refused: a retry after a refusal is a new key."""
        base = change_set_key(name, cursor, to, f, dd, part)
        return conn.execute("select count(*) from api.change_sets where attributed_to = %s and status in ('invalid','rejected','conflict') "
                            "and (idempotency_key = %s or starts_with(idempotency_key, %s))",
                            (self.agent, base, base + "|r")).fetchone()[0]

    def submit_own(self, conn, cur, key: str, d: Detector, ops: list, rid: str) -> dict:
        """Submit, and make sure the change set is ours: a key someone else took first (the kernel answers with
        their change set, or refuses it as a duplicate) is moved past with a key bound to this run."""
        def go(k):
            try:
                return submit(cur, {"idempotency_key": k, "attributed_to": self.agent, "target_scope": d.scope, "ops": ops}, rid)
            except psycopg.errors.UniqueViolation:
                return None
        res = go(key)
        mine = res is not None and conn.execute("select 1 from api.change_sets where id = %s and attributed_to = %s",
                                                 (res["id"], self.agent)).fetchone()
        if not mine:
            res = go(f"{key}|run:{rid}")
        if res is None or res.get("status") in REFUSED:
            raise RuntimeError(f"the kernel did not take the events: {res}")
        return res

    def find(self, owner, d: Detector, anchors) -> list[Match]:
        """The matches, in a read-only transaction with a statement timeout, SQL and Python alike. The owner's
        role is checked inside the transaction, before anything is returned."""
        try:
            with owner.transaction():
                owner.execute("set transaction read only")
                owner.execute(f"set local statement_timeout = {int(self.timeout_ms)}")
                if d.find:
                    found = list(d.find(owner, anchors))
                else:
                    cur = owner.execute(d.sql, {"anchors": anchors})
                    found = _matches(cur.fetchall(), [c.name for c in cur.description])
                if not self.same(owner, d.owner):
                    raise Refused(f"detector {d.name}: its query changed the role of its connection; the results are dropped")
                return found
        except Refused:
            self.drop(d.owner)
            raise

    def fail(self, conn, d: Detector, error: Exception, reported: Optional[str] = None) -> None:
        """A failed run, with the error: the record that this detector stopped, and why."""
        try:
            info = {"detector": d.name, "failed": True, **({"reported": reported} if reported else {})}
            with conn.cursor() as cur:
                cur.execute("select api.start_run(%s, null, '[]', %s)", (self.module, _jsonb(info)))
                rid = cur.fetchone()[0]
                cur.execute("select api.finish_run(%s, 'failed', %s)", (rid, _jsonb({"error": f"{type(error).__name__}: {error}"[:500]})))
        except Exception:
            pass

    # ------------------------------------------------------------ what is stored, as claims
    def stored(self, conn, d: Detector, anchors: Optional[list] = None, keys: Optional[list] = None) -> dict[str, Stored]:
        """The matches that hold. Only claims attributed to this agent count: nobody else can say what this
        detector found. With anchors, only the matches that name an anchor or are among `keys` are read."""
        det, me = detector_iri(d.name), self.agent
        rows = conn.execute(
            "select distinct on (u.object_entity) u.object_entity, i.subject, k.object_entity, t.id "
            "from api.claims i "
            "join api.claims u on u.subject = i.subject and u.predicate = %(used)s and u.status <> 'deprecated' and u.attributed_to = %(me)s "
            "join api.claims k on k.subject = i.subject and k.predicate = %(dct)s and k.status <> 'deprecated' and k.attributed_to = %(me)s "
            "join api.claims t on t.subject = i.subject and t.predicate = %(type)s and t.status <> 'deprecated' and t.attributed_to = %(me)s "
            "join api.applied a on a.change_set = i.change_set "
            "where i.predicate = %(instr)s and i.object_entity = %(det)s and i.status <> 'deprecated' and i.attributed_to = %(me)s "
            "and (%(anchors)s::text[] is null "
            "     or exists (select 1 from api.claims r where r.subject = i.subject and r.predicate = %(rel)s and r.status <> 'deprecated' "
            "                and r.attributed_to = %(me)s and r.object_entity = any(%(anchors)s::text[])) "
            "     or exists (select 1 from api.claims l where l.subject = u.object_entity and l.predicate = %(label)s and l.status <> 'deprecated' "
            "                and l.attributed_to = %(me)s and l.object_value->>'@value' = any(%(keys)s::text[]))) "
            "order by u.object_entity, a.seq desc",
            {"used": USED, "dct": DCT_TYPE, "type": RDF_TYPE, "instr": INSTRUMENT, "det": det, "me": me, "rel": RELATION,
             "label": LABEL, "anchors": anchors, "keys": keys or []}).fetchall()
        holding = [r for r in rows if r[2] == FORMED]
        if not holding:
            return {}
        nodes: dict[str, list] = {}
        for s, o in conn.execute("select subject, object_entity from api.claims where subject = any(%s) and predicate = %s "
                                 "and status <> 'deprecated' and attributed_to = %s", ([r[1] for r in holding], RELATION, me)):
            nodes.setdefault(s, []).append(o)
        labels = {s: (v, str(i)) for s, v, i in conn.execute(
            "select subject, object_value->>'@value', id from api.claims where subject = any(%s) and predicate = %s "
            "and status <> 'deprecated' and attributed_to = %s order by recorded_at", ([r[0] for r in holding], LABEL, me))}
        return {labels[m][0]: Stored(labels[m][0], tuple(sorted(nodes.get(ev, []))), str(t), labels[m][1])
                for m, ev, _, t in holding if m in labels}

    def known(self, conn, d: Detector, formed: list[Match]) -> set[str]:
        who = [match_iri(d.name, m.key) for m in formed]
        if not who:
            return set()
        return {r[0] for r in conn.execute("select distinct subject from api.claims where subject = any(%s) and predicate = %s "
                                           "and status <> 'deprecated' and attributed_to = %s", (who, LABEL, self.agent))}


def changed(owner, change_sets: list) -> list[str]:
    """Entities named by the claims these change sets added, or whose status they changed, or that they scored
    (belief) or verified, as the owner reads them: the subjects and the entity objects, the anchors a detector
    restricts its query to."""
    rows = owner.execute(
        "select c.subject, c.object_entity from kernel.claim c where c.change_set = any(%(cs)s) "
        "union select c.subject, c.object_entity from kernel.claim_status s join kernel.claim c on c.id = s.claim_id "
        "where s.change_set = any(%(cs)s) "
        "union select c.subject, c.object_entity from kernel.belief b join kernel.claim c on c.id = b.claim_id "
        "where b.change_set = any(%(cs)s) "
        "union select c.subject, c.object_entity from kernel.verification v join kernel.claim c on c.id = v.claim_id "
        "where v.change_set = any(%(cs)s)", {"cs": change_sets}).fetchall()
    return sorted({x for r in rows for x in r if x})


def _jsonb(v):
    from psycopg.types.json import Jsonb
    return Jsonb(v)

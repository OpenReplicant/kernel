#!/usr/bin/env python3
"""map-gaps as a reactor on the detector engine (M6-T0, D-110), on a throwaway kernel: it reads the kernel's
applied order from a cursor, so a gap forms when the claims that cause it are applied and closes when they are
answered, and nothing more happens when nothing was applied; the cursor survives a restart. Drops the database
at the end.

Needs Postgres 16+ (a superuser) and this repository installed; skips (exit 0) when one is missing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/detectors/tests")]
from harness import Kernel, connect, skip  # noqa: E402

try:
    from detectors.engine import Engine
    from map_gaps.detector import AGENT, DETECTOR, MODULE
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")

ORG = "http://www.w3.org/ns/org#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OA = "http://www.w3.org/ns/oa#"
K = Kernel("mg")
post, sam, unit = "urn:kk:org:acme:post:buyer", "urn:kk:person:acme:sam", "urn:kk:org:acme:unit:purchasing"


def claim(s, p, o):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": o}, "evidence_type": "extracted",
            "references": [{"source_uri": "urn:kk:test:interview", "quote": "the buyer, Sam, sits in purchasing"}]}


def gap_annotations():
    rows = K.k.execute("select c.subject, t.object_entity as kind from kernel.claim c "
                       "join kernel.claim t on t.subject = c.subject and t.predicate = 'http://purl.org/dc/terms/type' "
                       "where c.predicate = %s and c.object_entity = %s and c.attributed_to = %s "
                       "and kernel.current_status(c.id) <> 'deprecated' and kernel.current_status(t.id) <> 'deprecated'",
                       (RDF_TYPE, OA + "Annotation", AGENT)).fetchall()
    return sorted(r["kind"].rsplit(":", 1)[1] for r in rows)


def main():
    K.start()
    conn = connect("kkm_map_gaps")
    new = lambda: Engine([DETECTOR], "kkm_map_gaps", AGENT, MODULE)   # noqa: E731
    eng = new()

    def go(e=None):
        out = (e or eng).run_pass(conn)
        K.wait_applied()
        return out["map-gaps"]

    K.check("an empty kernel: no gaps", go()["formed"] == 0 and gap_annotations() == [])
    K.check("nothing applied since: idle", go()["status"] == "idle")

    n = K.seq()
    K.say([claim(post, RDF_TYPE, ORG + "Post")])
    r = go()
    K.check("a post is applied: the reactor wakes from the cursor and the gaps form (unheld, no unit)",
            r["formed"] == 2 and gap_annotations() == ["no-unit", "unheld"], f"{r} {gap_annotations()}")
    K.check("each gap is an event of the detector", sorted(e[1] for e in K.events(n, "map-gaps")) == ["formed", "formed"])
    cursor = K.k.execute("select max((info->>'cursor')::bigint) as c from kernel.run where agent = %s and status = 'succeeded'", (AGENT,)).fetchone()["c"]
    K.check("the cursor was stored with the run", cursor is not None and cursor >= n + 1, str(cursor))

    before = K.k.execute("select count(*) as n from kernel.change_set where attributed_to = %s", (AGENT,)).fetchone()["n"]
    go(new())      # a restart: the cursor comes from the runs
    go(new())
    K.check("after a restart nothing is repeated: no change set is added",
            K.k.execute("select count(*) as n from kernel.change_set where attributed_to = %s", (AGENT,)).fetchone()["n"] == before)

    n = K.seq()
    K.say([claim(sam, ORG + "holds", post)])
    r = go(new())
    K.check("an answer is applied while it was down: only that gap closes (a restarted reactor picks it up)",
            r["dissolved"] == 1 and gap_annotations() == ["no-unit"], f"{r} {gap_annotations()}")
    K.check("the closed gap is retracted, not deleted",
            K.k.execute("select count(*) as n from kernel.claim c where c.attributed_to = %s and kernel.current_status(c.id) = 'deprecated'", (AGENT,)).fetchone()["n"] >= 4)

    n = K.seq()
    K.say([claim(post, ORG + "postIn", unit)])
    r = go()
    K.check("the last answer closes the last gap", r["dissolved"] == 1 and gap_annotations() == [], f"{r} {gap_annotations()}")
    K.check("the events say what closed, in order", [e[1] for e in K.events(0, "map-gaps")] == ["formed", "formed", "dissolved", "dissolved"], str(K.events(0, "map-gaps")))
    K.finish("D-110 map-gaps reactor checks passed")


try:
    main()
finally:
    K.stop()

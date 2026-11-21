#!/usr/bin/env python3
"""map-gaps on the detector engine, first look (review finding B6, D-110): annotations the earlier worker left for
gaps that have since closed are retracted on the first full pass, and the annotations of gaps that are still
open are left alone. Throwaway kernel; skips (exit 0) when Postgres or the install is missing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/detectors/tests")]
from harness import Kernel, connect, run, skip, submit  # noqa: E402

try:
    from detectors.engine import Engine
    from map_gaps.detector import AGENT, DETECTOR, MODULE, change_set
except ImportError as e:
    skip(f"not importable ({e}); pip install -e .")

ORG = "http://www.w3.org/ns/org#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
K = Kernel("mgr")


def claim(s, p, o):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": {"entity": o}, "evidence_type": "extracted",
            "references": [{"source_uri": "urn:kk:test:interview", "quote": "a post exists"}]}


def statuses(subject):
    return [r["st"] for r in K.k.execute("select kernel.current_status(c.id) as st from kernel.claim c where c.subject = %s "
                                          "order by c.recorded_at, c.predicate", (subject,)).fetchall()]


def main():
    K.start()
    K.say([claim("urn:kk:org:acme:post:buyer", RDF_TYPE, ORG + "Post"), claim("urn:e:step1", RDF_TYPE, "urn:p:Thing")])
    prem = str(K.k.execute("select id from kernel.claim where subject = 'urn:e:step1'").fetchone()["id"])
    # what the old worker wrote earlier, for a gap that has since closed
    g = {"iri": "urn:kk:gap:orphan", "kind": "urn:kk:gap-kind:no-owner", "target": "urn:e:step1", "text": "who owns step1?", "premises": [prem]}
    cs = change_set(g)
    with connect("kkm_map_gaps") as c, c.cursor() as cur:
        with run(cur, MODULE) as (rid, _):
            submit(cur, cs, rid)
    K.wait_applied()
    check = K.check
    check("setup: the old annotation is current", statuses("urn:kk:gap:orphan") == ["normal"] * 4, str(statuses("urn:kk:gap:orphan")))

    conn = connect("kkm_map_gaps")
    eng = Engine([DETECTOR], "kkm_map_gaps", AGENT, MODULE)
    for _ in range(3):
        eng.run_pass(conn)
        K.wait_applied()
    check("B6 the first pass retracts the old worker's annotation for the closed gap", statuses("urn:kk:gap:orphan") == ["deprecated"] * 4, str(statuses("urn:kk:gap:orphan")))
    live = K.k.execute("select c.subject from kernel.claim c where c.predicate = %s and c.object_entity = 'http://www.w3.org/ns/oa#Annotation' "
                       "and c.attributed_to = %s and kernel.current_status(c.id) <> 'deprecated'", (RDF_TYPE, AGENT)).fetchall()
    check("B6 the gaps that are open are annotated and stay so (unheld, no unit)", len(live) == 2, str(live))
    K.finish("D-110 map-gaps reconcile checks passed")


try:
    main()
finally:
    K.stop()

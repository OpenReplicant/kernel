#!/usr/bin/env python3
"""Detector core, without a database (D-110): the diff of matches, the claims that record events, the
idempotency key, the registry file. Imports its siblings by path: no package install needed."""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]
import types  # noqa: E402

# the engine needs kk.lib (psycopg) only to run; the registry loader imports it, so stub it when it is absent
try:
    import kk.lib  # noqa: F401
except ImportError:
    kk = types.ModuleType("kk"); lib = types.ModuleType("kk.lib")
    lib.connect = lib.run = lib.submit = None
    sys.modules.update({"kk": kk, "kk.lib": lib})
from detectors.core import (DISSOLVED, FORMED, Match, Stored, change_set_key, diff, event_iri, event_ops,  # noqa: E402
                            match_iri)
from detectors.registry import load  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


a, b, c = Match("a", ("n1",), ("c1",)), Match("b", ("n2", "n3"), ("c2",)), Match("c", ("n4",), ("c3",))
stored = {"b": Stored("b", ("n2", "n3"), "t-b"), "z": Stored("z", ("n9",), "t-z")}

f, d = diff([a, b], stored, None)
check([m.key for m in f] == ["a"] and [s.key for s in d] == ["z"], "a full look: a forms, z dissolves, b stays")
f, d = diff([a, b], stored, ["n1"])
check([m.key for m in f] == ["a"] and d == [], "anchored on n1: z names n9, which did not change, so it is not looked at")
f, d = diff([], stored, ["n9"])
check(f == [] and [s.key for s in d] == ["z"], "anchored on a node a stored match names: it dissolves")
f, d = diff([a, c], {"a": Stored("a", ("n1",), "t")}, ["n1"])
check([m.key for m in f] == ["c"], "a match found now and not stored forms even if the query went beyond its anchors")
check(diff([a, b], {"a": Stored("a", ("n1",), "t"), "b": Stored("b", ("n2", "n3"), "t")}, None) == ([], []), "nothing changed: nothing recorded")

ops = event_ops("det", [a], [Stored("z", ("n9",), "t-z")], 7, "2026-01-01T00:00:00+00:00", set())
subjects = {o["subject"] for o in ops}
check(subjects == {match_iri("det", "a"), event_iri("det", "a", FORMED, 7), event_iri("det", "z", DISSOLVED, 7)},
      "events and the new match are the subjects; a match already stored is not described again")
check(all(o["evidence_type"] == "inferred" and o["derived_from"] for o in ops), "every claim is inferred, from premises")
dz = [o for o in ops if o["subject"] == event_iri("det", "z", DISSOLVED, 7)]
check(all(o["derived_from"] == ["t-z"] for o in dz), "a dissolved event is derived from the event that formed the match")
check(any(o["predicate"].endswith("#used") and o["object"] == {"entity": match_iri("det", "z")} for o in dz), "it uses the match")
check(not any(o["subject"] == match_iri("det", "a") for o in event_ops("det", [a], [], 8, "t", {match_iri("det", "a")})),
      "a match that re-forms is not described again")
try:
    event_ops("det", [Match("x", ("n",), ())], [], 1, "t", set())
    check(False, "a match with no premises is refused")
except ValueError:
    check(True, "a match with no premises is refused")

k1 = change_set_key("det", 3, 7, [a], [])
check(k1 == change_set_key("det", 3, 7, [a], []) and k1 != change_set_key("det", 3, 7, [b], []),
      "the idempotency key is the range and the outcome, not the time")

with tempfile.TemporaryDirectory() as t:
    p = Path(t) / "d.yaml"
    p.write_text("detectors:\n- name: unheld-post\n  owner: kkm_x\n  sql: select 1\n- name: other\n  owner: kkm_x\n  sql: select 2\n  anchored: false\n")
    ds = load(p)
    check([x.name for x in ds] == ["unheld-post", "other"] and ds[1].anchored is False, "a registry file lists detectors")
    p.write_text("detectors:\n- name: Bad Name\n  owner: r\n  sql: select 1\n")
    try:
        load(p)
        check(False, "a bad name is refused")
    except ValueError:
        check(True, "a bad name is refused")
print(f"{ok} checks passed")

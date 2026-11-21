#!/usr/bin/env python3
"""D-106 unit checks: a pinned span is judged against its file with no database and no model."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "verifier-quotes"))  # watcher_spans reuses its check
from watcher_spans.check import judge, lines_of, read, verdicts  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


FILE = "alpha\nthe chain fits 1/2 x 3/32\nomega\n"
Q = "the chain fits 1/2 x 3/32"
check(lines_of("L2") == (2, 2) and lines_of("L2-4") == (2, 4) and lines_of("p3") is None and lines_of("L4-2") is None,
      "a line locator is Lnn or Lnn-mm; anything else is not checked")
check(judge(Q, "L2", FILE) is None, "unchanged: nothing")
check(judge(Q, "L2", "alpha\n  the chain fits 1/2 x 3/32 \nomega") is None, "a quote inside its line (extra spaces around it) is unchanged")
check(judge("the chain fits 1/2 x 3/32\nomega", "L2-3", FILE) is None, "unchanged over several lines")
check(judge(Q, "L2", "new first line\n" + FILE) == {"why": "moved", "moved_to": "L3"}, "moved one line down: where it went")
check(judge("the chain fits 1/2 x 3/32\nomega", "L2-3", "x\ny\n" + FILE) == {"why": "moved", "moved_to": "L4-5"},
      "moved, several lines: the new range")
check(judge(Q, "L2", "alpha\nthe chain fits 1/8 inch\nomega") == {"why": "changed"}, "edited: changed")
check(judge(Q, "L1", "x\n" + Q + "\ny\n" + Q) == {"why": "changed", "found_at": ["L2", "L4"]},
      "found at several other places: changed, listing them")
check(judge(Q, "L2", None) == {"why": "gone"}, "no file: gone")
check(judge(Q, "p2", None) is None and judge(Q, "section 3", FILE) is None, "a locator that is not a line range: not checked")

with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
    (Path(b) / "dir").mkdir()
    (Path(b) / "dir/f.txt").write_text("hello")
    check(read([Path(a), Path(b)], "file:dir/f.txt") == "hello", "a file is found in the second of several roots")
    check(read([Path(a)], "file:dir/f.txt") is None, "and is gone when no root has it")
    check(read([Path(a)], "file:../../etc/passwd") is None, "a path out of the root is not read")

check(verdicts({"c": [{"why": "gone"}]}, {}) == [("c", "inconclusive", {"check": "pinned span", "changed": [{"why": "gone"}]})],
      "a claim citing a changed span is flagged")
check(verdicts({"c": [{"why": "gone"}]}, {"c": "inconclusive"}) == [], "a flagged claim is not flagged again")
check(verdicts({"c": []}, {"c": "inconclusive"}) == [("c", "supported", {"check": "pinned span", "changed": []})],
      "its spans as pinned again: restored")
check(verdicts({"c": []}, {}) == [] and verdicts({"c": []}, {"c": "supported"}) == [], "in step: nothing")
check(verdicts({"c": [{"why": "moved"}]}, {"c": "supported"})[0][1] == "inconclusive", "restored, then changed again: flagged again")
print(f"D-106 span watcher unit checks passed ({ok}/{ok})")

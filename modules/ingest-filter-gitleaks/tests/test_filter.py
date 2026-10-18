"""Ingest filter checks (M3-T4): a large file is scanned in chunks and a finding keeps its line.

  python modules/ingest-filter-gitleaks/tests/test_filter.py      (needs gitleaks on PATH)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ingest_filter.__main__ as f  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, detail)


# A fake key built at run time, so this file holds no secret-looking literal.
KEY = "AKIA" + "Q3EGRTZ7OVMW2KX5"
f.CHUNK, f.OVERLAP = 4096, 64
with tempfile.TemporaryDirectory() as d:
    repo = Path(d)
    rows = [f"<event n='{i}'/>\n" for i in range(3000)]
    rows[2500] = f"aws_access_key_id = {KEY}\n"
    (repo / "big.xes").write_text("".join(rows))
    (repo / "small.txt").write_text("nothing to see\n")
    (Path(d) / "copy").mkdir()
    chunks = f.split(repo / "big.xes", Path(d) / "copy" / "big.xes")
    text = "".join(rows)
    check("a large file is split into overlapping chunks that keep its suffix and cover it",
          len(chunks) > 5 and all(n.endswith(".xes") for n in chunks)
          and "".join((Path(d) / "copy" / n).read_text()[:f.CHUNK] for n in chunks) == text)
    (repo / "one.xes").write_text("<log>" + "<e/>" * 3000 + f"<x>{KEY}</x>" + "<e/>" * 3000 + "</log>")
    res = f.scan(repo, ["big.xes", "small.txt", "one.xes"])
    check("a finding in a chunk is reported at the file's own line, the file blocked",
          res["paths"] == ["small.txt"] and [(b["path"], b["line"]) for b in res["blocked"]] == [("big.xes", 2501), ("one.xes", 1)],
          res["blocked"])
    f.CHUNK = len("<log>" + "<e/>" * 3000) + 4       # the cut falls inside the key
    check("a secret across a cut is still found (chunks overlap)",
          f.scan(repo, ["one.xes"])["blocked"] == [{"path": "one.xes", "line": 1, "rule": "aws-access-token"}])
print(f"ingest filter checks passed ({sum(checks)}/{len(checks)})" if all(checks) else "ingest filter checks FAILED")
sys.exit(0 if all(checks) else 1)

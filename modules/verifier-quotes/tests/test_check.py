"""Quote checks (M3-T6): a verbatim quote passes, a fabricated or paraphrased one is caught.

  python modules/verifier-quotes/tests/test_check.py
"""
import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verifier_quotes.check import match, verdict  # noqa: E402

TEXT = "Billing sends the invoice only after the goods\nhave shipped, and within two days of shipping.\n"
checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


with tempfile.TemporaryDirectory() as d:
    root, store = Path(d) / "repo", Path(d) / "store"
    (root / "docs").mkdir(parents=True)
    store.mkdir()
    (root / "docs/procedure.md").write_text(TEXT)
    data = "Ligature ﬁnding: “the dose” – halved.".encode()
    sha = hashlib.sha256(data).hexdigest()
    (store / sha).write_bytes(data)
    (store / ("0" * 64)).write_bytes(b"tampered")
    (Path(d) / "secret.txt").write_text("outside the root")

    def one(**ref):
        return verdict([ref], store, root)[0]

    check("a verbatim quote passes", match("only after the goods\nhave shipped", TEXT) == "exact")
    check("a quote copied across a line break passes, normalised",
          match("only after the goods have shipped", TEXT) == "normalised")
    check("a paraphrase is caught", match("only once the goods have shipped", TEXT) is None)
    check("a changed word is caught", match("within three days of shipping", TEXT) is None)
    check("case matters", match("billing sends the invoice", TEXT) is None)
    check("a file: source under the root: supported",
          one(source_uri="file:docs/procedure.md", quote="within two days of shipping") == "supported")
    check("a file: source under the root: a fabricated quote is refuted",
          one(source_uri="file:docs/procedure.md", quote="Billing waits for payment first.") == "refuted")
    check("an artifact: ligatures and typographic quotes and dashes normalised",
          one(artifact=sha, quote='finding: "the dose" - halved') == "supported")
    check("an artifact whose bytes do not match its sha256 is not judged",
          one(artifact="0" * 64, quote="tampered") is None)
    check("an artifact not in this store is not judged", one(artifact="1" * 64, quote="x") is None)
    check("a path outside the source root is not read", one(source_uri="file:../secret.txt", quote="outside") is None)
    check("an http source is not fetched", one(source_uri="https://example.org/a", quote="x") is None)
    check("one refuted quote among several refutes the claim",
          verdict([{"source_uri": "file:docs/procedure.md", "quote": "within two days of shipping"},
                   {"artifact": sha, "quote": "the dose doubled"}], store, root)[0] == "refuted")
    check("a refuted quote is not hidden by an unreadable source",
          verdict([{"source_uri": "https://example.org/a", "quote": "x"},
                   {"artifact": sha, "quote": "the dose doubled"}], store, root)[0] == "refuted")

print(f"quote checks passed ({sum(checks)}/{len(checks)})" if all(checks) else "quote checks FAILED")
sys.exit(0 if all(checks) else 1)

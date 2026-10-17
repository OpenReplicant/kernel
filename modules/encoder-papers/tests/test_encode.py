"""D-59 unit checks for the paper encoder."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/encoder-papers")]
from paper_encoder.encode import ABSTRACT, TITLE, encode, paper_iri, read  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


lines = [{"doc_id": 4983, "title": "A study.", "abstract": ["First sentence.", "Second one."]},
         {"_id": "x/1", "title": "Beir style", "text": "Plain text abstract."},
         {"id": 7, "title": "", "abstract": ""}]
papers = read("\n".join(json.dumps(l) for l in lines).encode() + b"\n\n")
check([p["id"] for p in papers] == ["4983", "x/1", "7"], "ids from doc_id, _id or id")
check(papers[0]["abstract"] == "First sentence. Second one.", "a list of sentences is joined with spaces")
check(papers[1]["abstract"] == "Plain text abstract.", "BEIR's text field is the abstract")
ops = encode(papers, "scifact", "ab" * 32)
by = {(o["subject"], o["predicate"]): o for o in ops}
s = paper_iri("scifact", 4983)
check(s == "urn:kk:paper:scifact:4983" and paper_iri("beir", "x/1") == "urn:kk:paper:beir:x-1", "paper IRIs")
check(by[(s, TITLE)]["object"] == {"value": {"@value": "A study."}} and (s, ABSTRACT) in by, "title and abstract")
check(len([o for o in ops if o["subject"] == paper_iri("scifact", 7)]) == 1, "an empty title or abstract is left out")
check(all(o["evidence_type"] == "deterministic" and o["references"][0]["artifact"] == "ab" * 32
          and o["references"][0]["span"]["type"] == "FragmentSelector" for o in ops), "deterministic, referenced by artifact and id")
try:
    read(b'{"title": "no id"}\n')
    check(False, "a line without an id is refused")
except ValueError:
    check(True, "a line without an id is refused")
print(f"paper encoder: {ok} checks passed")

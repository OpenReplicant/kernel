"""M5-T5 unit checks on the interviewer's extraction (no database, no network): a scripted answer gives its claims
quoting the message actually recorded; a model's items are kept only when they name known terms and quote the message."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interviewer import core  # noqa: E402

cfg = core.config()
text = cfg["answers"][0]["text"]
iri = "urn:kk:message:acme:acme-purchasing:7"
ops = core.scripted_ops(cfg, text, iri)
assert len(ops) == len(cfg["answers"][0]["ops"]) > 10
assert all(o["references"][0]["source_uri"] == iri and o["references"][0]["quote"] in text for o in ops)
assert core.scripted_ops(cfg, "something else", iri) == []

said = "The buyer raises a purchase order."
answer = json.dumps([["org:post:buyer", "a", "ORG:Post", "The buyer"],
                     ["proc:step:raise-a-purchase-order", "BBO:has_resource", "org:post:buyer", "The buyer raises"],
                     ["org:post:buyer", "a", "ORG:Post", "words the person never said"],      # not quoted: dropped
                     ["org:post:buyer", "invented", "ORG:Post", "The buyer"],                   # unknown predicate: dropped
                     ["somewhere:else", "a", "ORG:Post", "The buyer"]])                         # unknown subject: dropped
got = core.parse_model("<think>x</think>" + answer, cfg, said, iri)
assert [o["predicate"].rsplit("#", 1)[-1] for o in got] == ["type", "has_resource"], got
assert got[1]["object"] == {"entity": "urn:kk:org:acme:post:buyer"} and got[1]["evidence_type"] == "extracted"
assert core.parse_model("no json here", cfg, said, iri) == []
print("ok: interviewer extraction (5 checks)")

"""The substrate profile's write path (D-105) without a database: the claim a `say` or `create_item` builds is the
claim encoder-substrate makes from the rows Substrate's store would hold for the same statement, apart from who
the author is; the tool table matches the encoder's properties; values and times read as Substrate reads them."""
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from encoder_substrate import encode as enc  # noqa: E402
from kernel_mcp import server, substrate as sub  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


run = asyncio.run
check(set(sub.TOOLS) == set(server.PROFILES["substrate"]), "the profile lists the tools the module defines")
check(set(sub.DATATYPE) <= set(enc.PROPERTIES) and {k for k in enc.PROPERTIES if k not in sub.DATATYPE} ==
      {k for k in enc.PROPERTIES if k.startswith("v1:field:")}, "every property of the encoder has a datatype here (the v1 relations are items)")
check(server.ARGUMENTS == {"from": "from_"} and server.public_schema(
    {"properties": {"from_": {}, "to": {}}, "required": ["from_", "to"]}) == {"properties": {"from": {}, "to": {}}, "required": ["from", "to"]},
      "path's `from` is the argument's public name")
try:
    server.KernelServer.allowed("nonsense")
    check(False, "an unknown profile is refused")
except server.KernelError as e:
    check("substrate" in str(e), "an unknown profile is refused and names substrate")
check(server.KernelServer.allowed("operator") is None, "operator is every tool")
check(sub.time_value("2026-11") == {"time": "+2026-11-00T00:00:00Z", "precision": 10}
      and sub.time_value("2026")["precision"] == 9 and sub.time_value("2026-11-17")["precision"] == 11, "times are read as Substrate's store reads them")
check(sub.short("urn:kk:substrate:Q13") == "Q13" and sub.short(enc.PROPERTIES["has-part"]) == "P527", "short ids")

SUBJECT, OTHER = "urn:kk:substrate:Q7", "urn:kk:substrate:Q9"
quote = {"source": "file:docs/a.md", "locator": "L1-2", "quote": "A says so."}


def built(prop, value, quals=None, evidence=None):
    class Conn:   # an item's existence is the one thing the database answers: every item exists here
        async def execute(self, *a, **k):
            class R:
                async def fetchall(self):
                    return [(1,)]
            return R()
    return run(sub.statement_op(Conn(), None, SUBJECT, prop, value, quals, evidence))


def exported(key, datatype, value, quals=(), evidence=None):
    """The same statement as a Substrate store row, through the encoder."""
    ents = [{"id": "Q7", "kind": "item", "key": None, "label": "seven"}, {"id": "Q9", "kind": "item", "key": None, "label": "nine"},
            {"id": "P1", "kind": "property", "key": key, "datatype": datatype}, {"id": "P2", "kind": "property", "key": "object-has-role", "datatype": "item"},
            {"id": "P3", "kind": "property", "key": "point-in-time", "datatype": "time"}]
    st = {"id": "x", "subject": "Q7", "property": "P1", "snak": "value", "rank": "normal", "qualifiers": list(quals),
          "value": value if datatype != "item" else None, "value_entity": value if datatype == "item" else None,
          "references": [{"id": "ref", "by_kind": "interpreter", "by_name": "n", "by_version": None, "run": None,
                          "span": {"source": evidence["source"], "locator": evidence["locator"], "text": evidence["quote"]} if evidence else None}]}
    sets, _, _ = enc.encode_store(ents, [st])
    return [o for o in sets[0][2] if o["op"] == "add_claim" and o["predicate"] != enc.RDFS_LABEL][0]


def same(a, b):
    drop = lambda op: json.dumps({**{k: v for k, v in op.items() if k != "ref"}, "references": [{k: v for k, v in r.items() if k != "source_uri"} for r in op["references"]]}, sort_keys=True)  # noqa: E731
    uri = lambda op: [r["source_uri"].split("?by=")[1] for r in op["references"]]  # noqa: E731
    return drop(a) == drop(b) and [u.split(":")[0] for u in uri(a)] == [u.split(":")[0] for u in uri(b)]


check(same(built("described-at-url", "https://example.org/x", None, quote),
           exported("described-at-url", "url", "https://example.org/x", (), quote)),
      "a URL with evidence: the encoder's claim (typed anyURI, testimony, quote and span)")
check(same(built("metric", "words"), exported("metric", "string", "words")), "a string without evidence")
check(same(built("measurement", 3.5, [{"property": "point-in-time", "value": "2026-11"}]),
           exported("measurement", "quantity", {"amount": 3.5},
                    [{"ord": 0, "property": "P3", "snak": "value", "value": sub.time_value("2026-11"), "value_entity": None}])),
      "a quantity with a time qualifier: decimal, qualifier keyed by its IRI")
check(same(built("has-part", "Q9", [{"property": "object-has-role", "value": "Q9"}]),
           exported("has-part", "item", "Q9", [{"ord": 0, "property": "P2", "snak": "value", "value": None, "value_entity": "Q9"}])),
      "an item value and a role qualifier: entities by IRI")
op = built("metric", "words")
check(op["evidence_type"] == "testimony" and op["references"][0]["source_uri"].startswith("urn:kk:substrate:reference:")
      and "?by=interpreter:mcp-client" in op["references"][0]["source_uri"], "a statement is testimony with an interpreter's reference")
check(built("metric", "words") == built("metric", "words"), "the same words build the same claim (the reference id is derived, not random)")
for bad in (("metric", ""), ("measurement", {"amount": 1, "lower": 0}), ("measurement", True), ("described-at-url", "no url"), ("point-in-time", "13th")):
    try:
        built(*bad)
        check(False, f"refused: {bad}")
    except server.KernelError:
        check(True, f"refused: {bad}")
try:
    built("made-up", "x")
    check(False, "an unknown property is refused")
except server.KernelError as e:
    check("migration or a person" in str(e), "an unknown property is refused: properties are governed")
labels = enc.Encoder([]).labels({"id": "Q7", "kind": "item", "key": None, "label": "L", "description": "D", "aliases": ["a"]})
check([o["predicate"] for o in labels] == [enc.RDFS_LABEL, enc.DCT_DESCRIPTION, enc.SKOS_ALT]
      and all(o["evidence_type"] == "deterministic" for o in labels), "an item's label, description and aliases are the encoder's")
print(f"{ok} checks passed")

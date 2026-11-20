"""encoder-substrate checks (D-103): Substrate's store rows become claims in Wikidata's and Substrate's terms.

  python modules/encoder-substrate/tests/test_encode.py
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from encoder_substrate.encode import (BS, SUB, WD, WDT, XSD, change_set, encode_store, evidence,  # noqa: E402
                                      supersede)

checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


fx = json.loads((HERE / "fixtures/store.json").read_text())
E, S = fx["entities"], fx["statements"]
sets, findings, counts = encode_store(E, S)
by_item = {i: ops for i, _, ops in sets}
ops = [o for _, _, ops in sets for o in ops]
claims = [o for o in ops if o["op"] == "add_claim"]
iri = lambda q: f"urn:kk:substrate:{q}"  # noqa: E731


def find(subject, pred, obj=None, within=None):
    return [o for o in (within or ops) if o["op"] == "add_claim" and o["subject"] == subject and o["predicate"] == pred
            and (obj is None or obj in (o["object"].get("entity"), (o["object"].get("value") or {}).get("@value")))]


def status_of(op, within):
    return [s for s in within if s["op"] == "set_status" and s["claim"] == op["ref"]]


def finding(kind):
    return [f for f in findings if f["finding"] == kind]


part = find(iri("Q10"), WDT + "P527", iri("Q11"))[0]
check("a part carries its role as a qualifier", part["qualifiers"] == {WDT + "P3831": iri("Q12")})
check("a role requiring an interface", find(iri("Q12"), SUB + "requiresInterface", iri("Q13")))
check("a candidate providing it", find(iri("Q14"), SUB + "providesInterface", iri("Q13")))
check("an item whose key names a pack term is its IRI and has no label claims",
      find(iri("Q12"), WDT + "P31", SUB + "Role") and not find(SUB + "Role", "http://www.w3.org/2000/01/rdf-schema#label")
      and "Q1" not in by_item)
check("other items get label, description and an alias claim each",
      len([o for o in by_item["Q10"] if o["op"] == "add_claim" and o["predicate"].endswith(("#label", "/description", "#altLabel"))]) == 4)
check("the prose is not encoded, only counted", counts["prose"] == 1 and "Long page text." not in json.dumps(ops))
check("a v1 relation is Backstage's", find(iri("Q15"), BS + "dependsOn", iri("Q16")))
unk = finding("unmapped-property")
check("an unknown property is a finding and its statements are not submitted",
      {f["key"] for f in unk} == {"v1:annotation:foo", None} and not any(
          o["object"].get("value", {}).get("@value") in ("bar", "baz") for o in claims))
uq = finding("unmapped-qualifier")
p2 = find(iri("Q10"), WDT + "P527", iri("Q14"))[0]
check("an unmapped qualifier is a finding and the statement is still submitted without it",
      uq and uq[0]["key"] == "v1:annotation:foo" and WDT + "P3831" in p2["qualifiers"] and len(p2["qualifiers"]) == 1)
check("a repeated qualifier property is a list", p2["qualifiers"][WDT + "P3831"] == [iri("Q12"), iri("Q13")])
old = find(iri("Q10"), WDT + "P973", "https://old.example/wb")[0]
st = status_of(old, by_item["Q10"])
check("a deprecated statement is set_status deprecated, retracted, and keeps its reason",
      st and st[0]["status"] == "deprecated" and st[0]["reason"] == "retracted"
      and old["qualifiers"] == {WDT + "P2241": "superseded by a newer page"})
new = find(iri("Q10"), WDT + "P973", "https://example.org/wb")[0]
check("a preferred statement is set_status preferred; a normal one has none",
      status_of(new, by_item["Q10"])[0]["status"] == "preferred" and not status_of(part, by_item["Q10"]))
check("a url is typed xsd:anyURI", new["object"]["value"] == {"@value": "https://example.org/wb", "@type": XSD + "anyURI"})
check("set_status follows its claim, which has a ref",
      by_item["Q10"][by_item["Q10"].index(old) + 1]["op"] == "set_status")
check("a parser is deterministic; a person, an interpreter or a rule is testimony (never inferred: no premises)",
      part["evidence_type"] == "deterministic" and find(iri("Q12"), SUB + "requiresInterface")[0]["evidence_type"] == "testimony"
      and find(iri("Q14"), SUB + "providesInterface")[0]["evidence_type"] == "testimony"
      and find(iri("Q11"), SUB + "providesInterface")[0]["evidence_type"] == "testimony"
      and not [o for o in claims if o["evidence_type"] == "inferred"])
many = find(iri("Q12"), SUB + "catalogId", "st-1")[0]
check("several references: one kernel reference each, the strongest evidence wins (parser)",
      len(many["references"]) == 3 and many["evidence_type"] == "deterministic"
      and evidence([{"by_kind": "person"}, {"by_kind": "interpreter"}]) == "testimony")
ref0 = part["references"][0]
check("a reference names who stated it (kind, name; the version when set)",
      ref0["source_uri"] == "urn:kk:substrate:reference:00000000-0000-0000-0000-0000000000a2?by=parser:map-skill"
      and "&version=2.1.0" in find(iri("Q14"), SUB + "providesInterface")[0]["references"][0]["source_uri"])
check("a span's text is the quote and its locator and source are kept",
      ref0["quote"] == "The workbench has a Postgres."
      and ref0["span"] == {"type": "FragmentSelector", "value": "L10-12", "source": "file:docs/framework.md"})
meas = find(iri("Q14"), SUB + "measurement")[0]
check("a quantity is its amount as xsd:decimal; its unit is a finding", meas["object"]["value"] == {"@value": "12.5", "@type": XSD + "decimal"}
      and finding("quantity-unit-not-encoded"))
check("a measurement's qualifiers: goal (an IRI) and time (xsd:dateTime text)",
      meas["qualifiers"] == {SUB + "forGoal": iri("Q17"), WDT + "P585": "2026-11-17T00:00:00Z"})
when = find(iri("Q11"), WDT + "P585")[0]
check("a time drops its leading +", when["object"]["value"] == {"@value": "2026-11-17T09:30:00Z", "@type": XSD + "dateTime"})
check("somevalue is a finding, skipped",
      finding("somevalue") and [o["object"]["value"]["@value"] for o in find(iri("Q11"), SUB + "metric")] == ["p99"])
unc = find(iri("Q11"), SUB + "metric", "p99")[0]
check("a statement with no reference is testimony, cites the statement, and is a finding",
      unc["evidence_type"] == "testimony"
      and unc["references"] == [{"source_uri": "urn:kk:substrate:statement:10000000-0000-0000-0000-00000000000f"}]
      and finding("uncited-statement"))
check("a statement on a property (a constraint) is a finding, skipped",
      finding("statement-on-property") and not [o for o in claims if o["subject"].startswith(WDT)]) and not [o for o in claims if o["subject"].startswith(WDT)]
check("every claim is attributed by the module, never 'extracted', and cites something",
      all(o["evidence_type"] != "extracted" and o["references"] for o in ops if o["op"] == "add_claim")
      and all(c["attributed_to"] == "urn:kk:agent:encoder-substrate" for c in (change_set(i, o) for i, _, o in sets)))
check("counts", counts["entities"] == len(sets) == 8 and counts["properties"] == 16 and counts["skipped"] == 4 and counts["empty"] == 5)

# Idempotency and superseding.
cs1, cs2 = change_set("Q10", by_item["Q10"]), change_set("Q10", copy.deepcopy(by_item["Q10"]))
rev = copy.deepcopy(S)
random_order = encode_store(E, list(reversed(rev)))[0]
check("the same rows give the same key, in any row order",
      cs1["idempotency_key"] == cs2["idempotency_key"] and cs1["idempotency_key"].startswith("substrate:Q10:")
      and len(cs1["idempotency_key"].split(":")[2]) == 16
      and {i: change_set(i, o)["idempotency_key"] for i, _, o in random_order}
      == {i: change_set(i, o)["idempotency_key"] for i, _, o in sets})
changed = copy.deepcopy(S)
changed[10]["value"] = "https://example.org/wb2"          # the preferred URL moved
sets2 = {i: o for i, _, o in encode_store(E, changed)[0]}
cs3 = change_set("Q10", sets2["Q10"])
check("a changed item has another key, an unchanged one keeps its", cs3["idempotency_key"] != cs1["idempotency_key"]
      and change_set("Q12", sets2["Q12"])["idempotency_key"] == change_set("Q12", by_item["Q12"])["idempotency_key"])
prior = [{"id": "11111111-0000-0000-0000-000000000000", "subject": new["subject"], "predicate": new["predicate"],
          "object_entity": None, "object_value": new["object"]["value"], "qualifiers": {}, "status": "preferred"},
         {"id": "22222222-0000-0000-0000-000000000000", "subject": iri("Q10"), "predicate": WDT + "P527",
          "object_entity": iri("Q11"), "object_value": None, "qualifiers": part["qualifiers"], "status": "normal"}]
supersede(cs3, prior)
sup = [o for o in cs3["ops"] if o["op"] == "set_status" and o["claim"][0] in "12"]
check("a restated claim is superseded by its new copy, a dropped one retracted",
      sup[0]["reason"] == "retracted" and sup[0]["expect_status"] == "preferred"
      and sup[1]["reason"] == "superseded" and sup[1]["replaced_by"].startswith("c") and sup[1]["expect_status"] == "normal")
check("an empty store submits nothing", encode_store([], [])[0] == [])
check("the pack terms exist in the packs", all(
    t in (Path(HERE.parents[2] / "ontology/wikidata.ttl").read_text() + Path(HERE.parents[2] / "ontology/substrate.ttl").read_text()
          + Path(HERE.parents[2] / "ontology/backstage.ttl").read_text())
    for t in ("wdt:P527", "wdt:P2241", "substrate:requiresInterface", "backstage:dependsOn", "wd:Q21510865", "substrate:better")))

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)

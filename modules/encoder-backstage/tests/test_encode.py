"""encoder-backstage checks (D-100): a catalog becomes claims in Backstage's terms, each citing its line.

  python modules/encoder-backstage/tests/test_encode.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from encoder_backstage.encode import BS, RDF_TYPE, RDFS_LABEL, encode, parse_ref, supersede  # noqa: E402

checks = []


def check(name, ok):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name)


text = (HERE / "fixtures/catalog-info.yaml").read_text()
lines = text.splitlines()
ops, findings, counts = encode(text, "0" * 64)


def find(s, p, o=None):
    return [op for op in ops if op["subject"] == s and op["predicate"] == p
            and (o is None or o in (op["object"].get("entity"), op["object"].get("value", {}).get("@value")))]


def cited(op):
    span = op["references"][0]["span"]
    return "\n".join(lines[span["start_line"] - 1:span["end_line"]])


check("every entity of a known kind is counted",
      counts == {"Group": 1, "System": 1, "Component": 2, "API": 1, "Resource": 1})
check("kinds keep Backstage's names", find("urn:kk:node:model-gateway", RDF_TYPE, BS + "Component")
      and find("urn:kk:api:model-api", RDF_TYPE, BS + "API") and find("urn:kk:system:engine", RDF_TYPE, BS + "System"))
check("components and resources are kk-tosca's node names", find("urn:kk:node:postgres", RDF_TYPE, BS + "Resource"))
ai_text = """apiVersion: backstage.io/v1alpha1
kind: AiResource
metadata: {name: setup, description: Bring the stack up}
spec: {type: skill, lifecycle: production, owner: group:operator}
"""
ai_ops, ai_findings, ai_counts = encode(ai_text, "1" * 64)
check("an AiResource (a skill) is a node, typed in Backstage's name, with its type",
      ai_counts == {"AiResource": 1} and not [f for f in ai_findings if f["finding"] != "dangling-ref"]
      and any(o["subject"] == "urn:kk:node:setup" and o["predicate"] == RDF_TYPE
              and o["object"].get("entity") == BS + "AiResource" for o in ai_ops)
      and any(o["subject"] == "urn:kk:node:setup" and o["predicate"] == BS + "type"
              and o["object"].get("value", {}).get("@value") == "skill" for o in ai_ops))
check("the title is the label", find("urn:kk:node:model-gateway", RDFS_LABEL, "Bifrost"))
check("relations as Backstage derives them",
      find("urn:kk:node:model-gateway", BS + "ownedBy", "urn:kk:group:platform")
      and find("urn:kk:node:model-gateway", BS + "partOf", "urn:kk:system:engine")
      and find("urn:kk:node:model-gateway", BS + "providesApi", "urn:kk:api:model-api")
      and find("urn:kk:node:agent", BS + "consumesApi", "urn:kk:api:model-api")
      and find("urn:kk:node:model-gateway", BS + "dependsOn", "urn:kk:node:postgres"))
ann = find("urn:kk:node:model-gateway", BS + "annotation", "bifrost")
check("an annotation keeps its key as a qualifier", ann and ann[0]["qualifiers"] == {BS + "annotationKey": "example.dev/otel-service"})
check("type, lifecycle and tags", find("urn:kk:node:model-gateway", BS + "type", "service")
      and find("urn:kk:node:model-gateway", BS + "lifecycle", "experimental") and find("urn:kk:node:model-gateway", BS + "tag", "gateway"))
check("every claim is deterministic and cites a line of the file",
      all(op["evidence_type"] == "deterministic" and op["references"][0]["artifact"] == "0" * 64 for op in ops))
check("a relation cites the line that states it",
      "consumesApis" in cited(find("urn:kk:node:agent", BS + "consumesApi", "urn:kk:api:model-api")[0]))
check("dependsOn cites its own line", "resource:postgres" in cited(find("urn:kk:node:model-gateway", BS + "dependsOn")[0]))
check("an unknown kind is a finding, not a crash", {"finding": "unknown-kind", "kind": "Template", "line": 57} in findings)
dangling = [f for f in findings if f["finding"] == "dangling-ref"]
check("a ref to an undefined entity is a finding, and still claimed",
      [f["ref"] for f in dangling] == ["vector-api"] and find("urn:kk:node:agent", BS + "consumesApi", "urn:kk:api:vector-api"))
check("refs: kind and namespace", parse_ref("component:default/x", "dependsOn") == ("component", "x")
      and parse_ref("platform", "owner") == ("group", "platform") and parse_ref("x", "dependsOn") == (None, "x"))
check("an empty file states nothing", encode("", "0" * 64) == ([], [], {}))

# Re-reading a changed file: the same statement is superseded by itself, a dropped one retracted.
cs = {"ops": [dict(op) for op in ops]}
kept = find("urn:kk:node:model-gateway", RDFS_LABEL, "Bifrost")[0]
prior = [{"id": "11111111-0000-0000-0000-000000000000", "subject": kept["subject"], "predicate": kept["predicate"],
          "object_entity": None, "object_value": {"@value": "Bifrost"}, "qualifiers": {}, "status": "normal"},
         {"id": "22222222-0000-0000-0000-000000000000", "subject": "urn:kk:node:gone", "predicate": RDF_TYPE,
          "object_entity": BS + "Component", "object_value": None, "qualifiers": None, "status": "normal"}]
supersede(cs, prior)
st = [op for op in cs["ops"] if op["op"] == "set_status"]
check("a restated claim is superseded by its new copy, a removed one retracted",
      st[0]["reason"] == "superseded" and st[0]["replaced_by"].startswith("c") and st[1]["reason"] == "retracted"
      and all(op["status"] == "deprecated" for op in st))

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)

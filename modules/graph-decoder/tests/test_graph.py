"""Graph projection over the real self-model (encoded offline from this repository).

Run: python modules/graph-decoder/tests/test_graph.py     (needs pyyaml, rdflib, pyshacl, jsonschema)
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/graph-decoder"), str(ROOT / "modules/self-encoder"),
                str(ROOT / "modules/validator-shacl")]
from graph_decoder.graph import build, cytoscape, mermaid, select  # noqa: E402
from self_encoder.encode import encode_file, list_files  # noqa: E402
from validator.core import expand  # noqa: E402

KK = "https://w3id.org/knowledge-kernel/ns#"


def self_model_claims():
    rows, n = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for rel in list_files(ROOT):
            for op in encode_file(ROOT, rel, Path(tmp))["ops"]:
                if op["op"] != "add_claim":
                    continue
                n += 1
                obj = op["object"]
                rows.append({"id": f"c{n}", "subject": expand(op["subject"]), "predicate": expand(op["predicate"]),
                             "object_entity": expand(obj["entity"]) if "entity" in obj else None,
                             "object_value": obj.get("value"), "qualifiers": op.get("qualifiers", {}),
                             "status": "normal", "belief": 0.5})
    return rows


G = build(self_model_claims())


def ids(keep):
    return {n["data"]["id"] for n in cytoscape(G, keep)["elements"]["nodes"]}


def test_kinds_and_edges_carry_claims():
    kinds = {}
    for n in G["nodes"].values():
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    assert kinds["module"] == 12 and kinds["chain"] == 7 and kinds["node"] == 13, kinds
    assert all(e["id"] and e["belief"] == 0.5 for e in G["edges"])
    assert G["nodes"]["urn:kk:module:validator-shacl"]["label"] == "Change-set validator (JSON Schema + SHACL)"


def test_module_filter():
    keep = ids(select(G, module="urn:kk:module:validator-shacl"))
    assert {"urn:kk:slot:shape-validator", "urn:kk:node:validator", "urn:kk:chain:validate-one",
            "urn:kk:chain:validate-one/record", "urn:kk:chain:validate-change-sets"} <= keep, keep
    assert "urn:kk:module:belief-corroboration-v0" not in keep


def test_slot_filter():
    keep = ids(select(G, slot="urn:kk:slot:chain-runner"))
    assert {"urn:kk:module:chain-runner-python", "urn:kk:module:chain-runner-rulego"} <= keep, keep


def test_chain_filter_shows_routing():
    data = cytoscape(G, select(G, chain="urn:kk:chain:validate-one"))["elements"]
    labels = {(e["data"]["source"].rsplit("/", 1)[1], e["data"]["label"], e["data"]["target"].rsplit("/", 1)[1])
              for e in data["edges"] if e["data"]["predicate"] == KK + "next"}
    assert labels == {("validate", "next [success]", "record"), ("validate", "next [failure]", "log_error")}, labels


def test_root_depth_and_mermaid():
    keep = select(G, root="urn:kk:node:postgrest", depth=1)
    assert {"urn:kk:node:db", "urn:kk:node:caddy"} <= keep
    text = mermaid(G, keep)
    assert text.startswith("flowchart LR") and "dependsOn 0.50" in text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"graph decoder tests passed ({len(tests)}; {len(G['nodes'])} nodes, {len(G['edges'])} edges)")

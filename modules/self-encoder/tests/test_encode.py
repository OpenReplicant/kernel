"""Every self-model file encodes to a change set the validator accepts, with a file artifact and a
line span on every claim. Offline: no database or services.

Run: python modules/self-encoder/tests/test_encode.py      (needs pyyaml, rdflib, pyshacl, jsonschema)
"""
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/self-encoder"), str(ROOT / "modules/validator-shacl")]
from self_encoder.encode import encode_file, list_files  # noqa: E402
from validator.core import validate  # noqa: E402

SLOTS = [f"urn:kk:slot:{s['id']}" for g in yaml.safe_load((ROOT / "ontology/slots.yaml").read_text())["groups"].values()
         for s in g]
CONTEXT = [(s, "rdf:type", "kk:Slot") for s in SLOTS]


def test_every_file_encodes_valid_and_referenced():
    files = list_files(ROOT)
    kinds = {"module.yaml": 0, "chains": 0, "compose.yaml": 0, ".ttl": 0, ".sql": 0}
    with tempfile.TemporaryDirectory() as artifacts:
        for rel in files:
            cs = encode_file(ROOT, rel, Path(artifacts))
            cs["generated_by"] = "00000000-0000-0000-0000-000000000001"
            ok, report = validate(cs, CONTEXT)
            assert ok, (rel, report)
            art = cs["ops"][0]
            assert art["op"] == "add_artifact" and (Path(artifacts) / art["sha256"]).exists(), rel
            claims = [op for op in cs["ops"] if op["op"] == "add_claim"]
            assert claims, f"{rel}: no claims"
            for op in claims:
                ref = op["references"][0]
                assert ref["artifact"] == art["sha256"] and ref["span"]["start_line"] >= 1, (rel, op)
                assert ref["span"]["end_line"] >= ref["span"]["start_line"], (rel, op)
            for k in kinds:
                kinds[k] += k in rel
    assert all(kinds.values()), kinds
    assert not any("_template" in f for f in files)


def test_spans_point_at_the_source_line():
    cs = encode_file(ROOT, "modules/validator-shacl/module.yaml", Path(tempfile.mkdtemp()))
    lines = (ROOT / "modules/validator-shacl/module.yaml").read_text().splitlines()
    fills = next(op for op in cs["ops"] if op.get("predicate") == "kk:fills")
    assert "shape-validator" in lines[fills["references"][0]["span"]["start_line"] - 1]
    version = next(op for op in cs["ops"] if op.get("predicate") == "owl:versionInfo")
    assert lines[version["references"][0]["span"]["start_line"] - 1].startswith("version:")


if __name__ == "__main__":
    test_every_file_encodes_valid_and_referenced()
    test_spans_point_at_the_source_line()
    print(f"self-encoder tests passed ({len(list_files(ROOT))} files)")

"""Chain decoder over the real self-model (encoded offline from this repository).

Run: python modules/chain-decoder-rulego/tests/test_compile.py     (needs pyyaml, rdflib, pyshacl, jsonschema)
Optional: KK_COMPILED_DIR=<dir> also writes the compiled chains there (to load them into RuleGo-Server).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "modules/chain-decoder-rulego"), str(ROOT / "modules/self-encoder"),
                str(ROOT / "modules/validator-shacl")]
from chain_decoder.compile import chains_from_claims, compile_all, render  # noqa: E402
from self_encoder.encode import encode_file, list_files  # noqa: E402
from validator.core import expand  # noqa: E402


def self_model_claims():
    rows, n = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for rel in list_files(ROOT):
            for op in encode_file(ROOT, rel, Path(tmp))["ops"]:
                if op["op"] == "add_claim":
                    n += 1
                    obj = op["object"]
                    rows.append({"id": f"c{n}", "subject": expand(op["subject"]), "predicate": expand(op["predicate"]),
                                 "object_entity": expand(obj["entity"]) if "entity" in obj else None,
                                 "object_value": obj.get("value"), "qualifiers": op.get("qualifiers", {}),
                                 "status": "normal"})
    return rows


CHAINS = chains_from_claims(self_model_claims())
RESULTS = compile_all(CHAINS, {iri: "urn:kk:module:test" for iri in CHAINS})
VERDICT = {iri.rsplit(":", 1)[1]: rc is not None for iri, (rc, _) in RESULTS.items()}


def test_verdicts():
    assert VERDICT == {
        "validate-change-sets": True, "validate-one": True, "submit-change-set": True,
        "rescore-belief": True, "submit-beliefs": True, "deploy-rulego-chains": True,
        "check-rulego-drift": True, "submit-decoder-claims": True,
        "ingest-filter": False, "scan-self": False}, VERDICT
    why = dict((k.rsplit(":", 1)[1], v[1]) for k, v in RESULTS.items())
    assert any("RuleGo runs inline JavaScript only" in r for r in why["ingest-filter"]), why["ingest-filter"]
    assert any("http /scan" in r for r in why["scan-self"]) and any("ingest-filter" in r for r in why["scan-self"])


def test_routing_and_runs():
    rc, _ = RESULTS["urn:kk:chain:validate-one"]
    nodes = {n["id"]: n for n in rc["metadata"]["nodes"]}
    links = {(c["fromId"], c["toId"], c["type"]) for c in rc["metadata"]["connections"]}
    assert rc["metadata"]["nodes"][0]["id"] == "validate"                      # entry first
    assert ("validate", "record__run", "Success") in links and ("validate", "log_error", "Failure") in links
    assert nodes["record__start"]["configuration"]["restEndpointUrlPattern"] == "${global.KK_API}/rpc/start_run"
    assert "urn:kk:chain:validate-one/record" in nodes["record__run"]["configuration"]["jsScript"]
    assert nodes["record"]["configuration"]["headers"]["Authorization"] == "Bearer ${global.KK_TOKEN_VALIDATOR}"
    assert "Authorization" not in nodes["validate"]["configuration"]["headers"]   # not a kernel call
    loop, _ = RESULTS["urn:kk:chain:validate-change-sets"]
    each = next(n for n in loop["metadata"]["nodes"] if n["id"] == "each")
    assert each["type"] == "for" and each["configuration"] == {"range": "", "do": "chain:validate-one", "mode": 1}
    assert loop["metadata"]["endpoints"][0]["routers"][0]["from"]["path"] == "*/2 * * * * *"


def test_output_is_deterministic():
    again = compile_all(chains_from_claims(self_model_claims()), {iri: "urn:kk:module:test" for iri in CHAINS})
    assert all(render(RESULTS[i][0]) == render(again[i][0]) for i in RESULTS if RESULTS[i][0])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    out = os.environ.get("KK_COMPILED_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        for iri, (rc, _) in RESULTS.items():
            if rc:
                (Path(out) / f"{iri.rsplit(':', 1)[1]}.json").write_bytes(render(rc))
    print(f"chain decoder tests passed ({len(tests)}; runnable {sum(VERDICT.values())}/{len(VERDICT)})")

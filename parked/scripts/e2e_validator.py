"""T4 end to end, run inside the validator container (stdlib only):
an agent submits three change sets, the validator reads them from api.validation_queue, judges them
with its own /validate endpoint (fetching context claims through the API), and records the verdicts.

Env: KK_API, AGENT_JWT (kkm_self_encoder), VALIDATOR_JWT (kkm_validator).
Expected: valid auto-lane -> applied; extracted without quote -> invalid with the shape message;
complete module (fills a slot known only from the packs) -> awaiting_review.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ["KK_API"]


def call(method, path, token, body=None, base=API):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {base}{path} -> {e.code}: {e.read().decode()[:500]}")


agent, validator = os.environ["AGENT_JWT"], os.environ["VALIDATOR_JWT"]
for _ in range(30):                      # the service may still be starting
    try:
        urllib.request.urlopen("http://localhost:8000/health", timeout=2)
        break
    except OSError:
        time.sleep(1)
run = call("POST", "/rpc/start_run", agent, {"module": "urn:kk:module:self-encoder"})
stamp = str(time.time())
src = {"source_uri": "file:scripts/e2e_validator.py"}
M = "urn:kk:module:e2e-test"


def cs(key, ops, scope="urn:kk:scope:default"):
    return {"cs": {"idempotency_key": f"e2e:{key}:{stamp}", "attributed_to": "urn:kk:agent:self-encoder",
                   "generated_by": run, "target_scope": scope, "ops": ops}}


def claim(s, p, o, ev="deterministic", refs=(src,)):
    return {"op": "add_claim", "subject": s, "predicate": p, "object": o, "evidence_type": ev, "references": list(refs)}


submitted = {
    "valid": call("POST", "/rpc/submit_change_set", agent, cs("valid", [
        claim("urn:kk:test:e2e", "rdfs:comment", {"value": {"@value": "validated end to end"}})])),
    "no-quote": call("POST", "/rpc/submit_change_set", agent, cs("no-quote", [
        claim("urn:kk:test:e2e", "rdfs:comment", {"value": {"@value": "x"}}, ev="extracted")])),
    "module": call("POST", "/rpc/submit_change_set", agent, cs("module", [
        claim(M, "rdf:type", {"entity": "kk:Module"}),
        claim(M, "rdfs:label", {"value": {"@value": "E2E test module"}}),
        claim(M, "kk:version", {"value": {"@value": "0.1.0"}}),
        claim(M, "kk:moduleKind", {"entity": "kk:Processor"}),
        claim(M, "kk:fills", {"entity": "urn:kk:slot:shape-validator"}),
        claim(M, "kk:runsAs", {"entity": "urn:kk:agent:validator"}),
        claim(M, "dcterms:license", {"entity": "https://spdx.org/licenses/Apache-2.0"})], scope="urn:kk:scope:vocab")),
}
ids = {k: v["id"] for k, v in submitted.items()}

queue = {row["id"]: row for row in call("GET", "/validation_queue", validator)}
missing = set(ids.values()) - set(queue)
assert not missing, f"validator cannot see {missing} in api.validation_queue"
for name, cs_id in ids.items():
    verdict = call("POST", "/validate", "", queue[cs_id], base="http://localhost:8000")
    call("POST", "/rpc/record_validation", validator,
         {"change_set_id": cs_id, "ok": verdict["ok"], "report": verdict["report"]})

rows = {r["id"]: r for r in call("GET", "/change_sets?id=in.(" + ",".join(ids.values()) + ")", agent)}
status = {name: rows[cs_id]["status"] for name, cs_id in ids.items()}
print("statuses:", status)
messages = [v["message"] for v in rows[ids["no-quote"]]["validation_report"]["shape_violations"]]
print("no-quote report:", messages)
ok = (status == {"valid": "applied", "no-quote": "invalid", "module": "awaiting_review"}
      and "Invariant 1: extracted claims carry the supporting quote." in messages)
print("T4 end-to-end", "passed" if ok else "FAILED")
sys.exit(0 if ok else 1)

"""T5 end to end, run on the host against Caddy: an agent submits change sets and the Python runner's
validator chains (validate-change-sets -> validate-one) judge and record them with no other help.

Env: KK_URL (default http://127.0.0.1:8080/api), AGENT_JWT (kkm_self_encoder).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("KK_URL", "http://127.0.0.1:8080/api")
TOKEN = os.environ["AGENT_JWT"]


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


run = call("POST", "/rpc/start_run", {"module": "urn:kk:module:self-encoder"})
stamp = str(time.time())


def submit(key, evidence, refs):
    return call("POST", "/rpc/submit_change_set", {"cs": {
        "idempotency_key": f"e2e-runner:{key}:{stamp}", "attributed_to": "urn:kk:agent:self-encoder",
        "generated_by": run, "target_scope": "urn:kk:scope:default",
        "ops": [{"op": "add_claim", "subject": "urn:kk:test:runner", "predicate": "rdfs:comment",
                 "object": {"value": {"@value": key}}, "evidence_type": evidence, "references": refs}]}})["id"]


ids = {"valid": submit("valid", "deterministic", [{"source_uri": "file:scripts/e2e_runner.py"}]),
       "no-quote": submit("no-quote", "extracted", [{"source_uri": "file:scripts/e2e_runner.py"}])}
started = time.monotonic()
while True:
    rows = call("GET", "/change_sets?id=in.(" + ",".join(ids.values()) + ")&select=id,status")
    status = {name: next(r["status"] for r in rows if r["id"] == cs_id) for name, cs_id in ids.items()}
    if "submitted" not in status.values() or time.monotonic() - started > 60:
        break
    time.sleep(0.5)
print(f"statuses after {time.monotonic() - started:.1f}s:", status)
ok = status == {"valid": "applied", "no-quote": "invalid"}
print("T5 end-to-end", "passed" if ok else "FAILED")
sys.exit(0 if ok else 1)

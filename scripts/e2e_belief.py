"""T7 end to end, run on the host against Caddy: two agents submit claims and the belief chain scores
them. Expected beliefs follow eval/belief-v0 (S / (S + C + 1)).

Env: KK_URL (default http://127.0.0.1:8080/api), AGENT_A_JWT (kkm_self_encoder), AGENT_B_JWT (kkm_graph_decoder).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("KK_URL", "http://127.0.0.1:8080/api")
AGENTS = {"a": ("urn:kk:agent:self-encoder", os.environ["AGENT_A_JWT"]),
          "b": ("urn:kk:agent:graph-decoder", os.environ["AGENT_B_JWT"])}
STAMP = str(int(time.time() * 1000))


def call(token, method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


runs = {k: call(tok, "POST", "/rpc/start_run", {}) for k, (_, tok) in AGENTS.items()}


def submit(agent, name, subject, predicate, value, source, **window):
    iri, token = AGENTS[agent]
    op = {"op": "add_claim", "subject": f"urn:kk:test:{subject}:{STAMP}", "predicate": predicate,
          "object": {"value": {"@value": value}}, "evidence_type": "deterministic",
          "references": [{"source_uri": f"file:{source}"}], **window}
    call(token, "POST", "/rpc/submit_change_set", {"cs": {
        "idempotency_key": f"e2e-belief:{name}:{STAMP}", "attributed_to": iri, "generated_by": runs[agent],
        "target_scope": "urn:kk:scope:default", "ops": [op]}})
    return op["subject"]


expect = {}
s = submit("a", "copy-a", "copied", "rdfs:comment", "x", "shared.md")
submit("b", "copy-b", "copied", "rdfs:comment", "x", "shared.md")
expect[s] = [0.5, 0.5]                                          # copied source: one independent source
s = submit("a", "indep-a", "independent", "rdfs:comment", "x", "one.md")
submit("b", "indep-b", "independent", "rdfs:comment", "x", "two.md")
expect[s] = [0.6667, 0.6667]                                    # two independent sources
s = submit("a", "conflict-a", "conflict", "kk:version", "1.0.0", "one.md")
submit("b", "conflict-b", "conflict", "kk:version", "2.0.0", "two.md")
expect[s] = [0.3333, 0.3333]                                    # single-valued property, overlapping
s = submit("a", "change-a", "change", "kk:version", "1.0.0", "one.md", valid_to="2026-10-01T00:00:00Z")
submit("b", "change-b", "change", "kk:version", "2.0.0", "two.md", valid_from="2026-10-01T00:00:00Z")
expect[s] = [0.5, 0.5]                                          # change over time, not a conflict

subjects = ",".join(f'"{x}"' for x in expect)
started = time.monotonic()
while True:
    rows = call(AGENTS["a"][1], "GET", "/claims?" + urllib.parse.urlencode(
        {"select": "subject,belief", "subject": f"in.({subjects})"}))
    got = {}
    for r in rows:
        got.setdefault(r["subject"], []).append(r["belief"] if r["belief"] is None else round(r["belief"], 4))
    if all(sorted(got.get(k, []), key=str) == v for k, v in expect.items()) or time.monotonic() - started > 90:
        break
    time.sleep(1)
for k, v in expect.items():
    print(f"{k.split(':')[3]:12} expected {v} got {got.get(k)}")
ok = all(sorted(got.get(k, []), key=str) == v for k, v in expect.items())
print(f"T7 end-to-end {'passed' if ok else 'FAILED'} after {time.monotonic() - started:.1f}s")
sys.exit(0 if ok else 1)

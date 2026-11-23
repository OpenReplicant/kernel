"""encoder-compose checks (D-125): a compose file becomes a service template and node templates in kk-tosca's terms,
each claim citing its lines; environment values are never read.

  python modules/encoder-compose/tests/test_encode.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from encoder_compose.encode import KKT, PROPERTY, RDF_TYPE, encode, node_type, supersede  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)


text = (HERE / "fixtures/compose.yaml").read_text()
lines = text.splitlines()
ops, findings, counts = encode(text, "0" * 64, "shop")
T = "urn:kk:service-template:shop"


def find(s, p, o=None, prop=None):
    return [op for op in ops if op["subject"] == s and op["predicate"] == p
            and (o is None or o in (op["object"].get("entity"), op["object"].get("value", {}).get("@value")))
            and (prop is None or (op.get("qualifiers") or {}).get(PROPERTY) == prop)]


def cited_line(op):
    return lines[op["references"][0]["span"]["start_line"] - 1]


check("the project is a service template whose version is the file", find(T, RDF_TYPE, KKT + "ServiceTemplate")
      and find(T, KKT + "hasArtifact", "urn:kk:artifact:" + "0" * 64))
check("each service is a node template, member of the template", all(find(f"urn:kk:node:{n}", KKT + "memberOf", T)
      for n in ("db", "api", "worker", "ghost")) and counts == {"services": 4})
check("the node type is the image's repository, without tag", find("urn:kk:node:db", KKT + "nodeType", "urn:kk:node-type:docker.io/library/postgres"))
check("an interpolated image is kept as written", find("urn:kk:node:api", KKT + "propertyValue", "${API_IMAGE:-ghcr.io/example/shop-api:1.4}", "image"))
check("node_type strips a tag and a digest, keeps a registry port", node_type("reg:5000/a/b:1.2@sha256:ab", "x") == "urn:kk:node-type:reg:5000/a/b"
      and node_type(None, "w") == "urn:kk:node-type:w")
check("a merged anchor's fields do not become claims (they stay in the file)", not find("urn:kk:node:api", KKT + "propertyValue", "true"))
dep = find("urn:kk:node:api", KKT + "dependsOn", "urn:kk:node:db")
check("a dependency with a condition is claimed, citing its line", dep and "db:" in cited_line(dep[0]))
check("list dependencies are claimed", find("urn:kk:node:worker", KKT + "dependsOn", "urn:kk:node:api") and find("urn:kk:node:worker", KKT + "dependsOn", "urn:kk:node:db"))
check("profiles, ports and volumes are property values citing their lines",
      find("urn:kk:node:worker", KKT + "propertyValue", "jobs", "profile")
      and "8080" in cited_line(find("urn:kk:node:api", KKT + "propertyValue", "127.0.0.1:8080:8080", "port")[0])
      and find("urn:kk:node:db", KKT + "propertyValue", "db-data:/var/lib/postgresql/data", "volume"))
env = [op["object"]["value"]["@value"] for op in find("urn:kk:node:api", KKT + "propertyValue", prop="environment")]
check("environment variables are claimed by name, in both forms", sorted(env) == ["DATABASE_URL", "LOG_LEVEL"]
      and find("urn:kk:node:db", KKT + "propertyValue", "POSTGRES_PASSWORD", "environment"), env)
check("no environment value is in any claim", not any("shop@db" in str(op["object"]) or "set in .env" in str(op["object"]) for op in ops))
check("a service with neither image nor build is a finding", any(f["finding"] == "no-image-or-build" and f["service"] == "ghost" for f in findings), findings)
check("every claim is deterministic and cites a line", all(op["evidence_type"] == "deterministic" and op["references"][0]["span"]["start_line"] >= 1 for op in ops))

# a re-read of a changed file supersedes what it states again and retracts the rest
cs = {"ops": [dict(op) for op in ops[:3]]}
prior = [{"id": "p1", "subject": ops[0]["subject"], "predicate": ops[0]["predicate"], "object_entity": ops[0]["object"].get("entity"),
          "object_value": None, "qualifiers": {}, "status": "normal"},
         {"id": "p2", "subject": "urn:kk:node:gone", "predicate": RDF_TYPE, "object_entity": KKT + "NodeTemplate",
          "object_value": None, "qualifiers": {}, "status": "normal"}]
supersede(cs, prior)
st = {op["claim"]: op for op in cs["ops"] if op["op"] == "set_status"}
check("a statement made again supersedes, a missing one is retracted", st["p1"]["reason"] == "superseded" and st["p2"]["reason"] == "retracted")

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)

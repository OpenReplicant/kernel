"""D-55 unit checks for the sandbox policy: the grant decides the container, and an ungranted request
is refused."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]
from kk.cli import module_agents  # noqa: E402
from kk.modules import grant, service  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def manifest(name):
    return yaml.safe_load((ROOT / f"modules/{name}/module.yaml").read_text())


policy = yaml.safe_load((ROOT / "deploy/policy.yaml").read_text())
obs = manifest("observer-prometheus")

g, denied = grant(policy, "observer-prometheus", obs)
check(not denied and g["reach"] == ["prometheus"] and not g["egress"], "the default policy grants what the demo modules ask")
for name in ("anomaly", "conformance", "actuator-compose", "encoder-eventlog",
             "encoder-papers", "recall-index", "recall-dense", "verifier-quotes"):
    check(not grant(policy, name, manifest(name))[1], f"{name} is granted by the default policy")

c = service("observer-prometheus", obs, "/x.env", "img", g)
svc = c["services"]["module-observer-prometheus"]
check(svc["networks"] == ["kernel-db", "kk-reach-prometheus"] and c["networks"] == {"kk-reach-prometheus": {"internal": True}},
      "a granted service is reached over its own internal network, nothing else")
check(svc["mem_limit"] == "256m" and svc["pids_limit"] == 64, "default limits")

_, denied = grant({"defaults": {"egress": False}}, "observer-prometheus", obs)
check(denied == ["reach prometheus"], "a request the policy leaves out is named")
check(grant({}, "anomaly", manifest("anomaly"))[1] == [], "no policy: a module that asks for no network installs")
check(grant({"modules": {"anomaly": {"internet": True}}}, "anomaly", {})[1] == ["unknown policy key 'internet'"],
      "a misspelt grant is refused, not ignored")

more = {"defaults": {"memory": "128m"}, "modules": {"observer-prometheus": {"reach": ["prometheus", "shop"],
                                                                           "egress": True, "memory": "1g", "pids": 200}}}
g, denied = grant(more, "observer-prometheus", obs)
c = service("observer-prometheus", obs, "/x.env", "img", g)
svc = c["services"]["module-observer-prometheus"]
check(not denied and svc["networks"] == ["kernel-db", "kk-reach-prometheus", "kk-reach-shop", "kk-egress"]
      and c["networks"]["kk-egress"] == {} and svc["mem_limit"] == "1g" and svc["pids_limit"] == 200,
      "an operator may grant more than the manifest asks: another service, a route out, larger limits")
check(service("anomaly", manifest("anomaly"), "/x.env", "img", grant(more, "anomaly", {})[0])
      ["services"]["module-anomaly"]["mem_limit"] == "128m", "defaults apply to modules without an entry")

vq = service("verifier-quotes", manifest("verifier-quotes"), "/x.env", "img")
svc = vq["services"]["module-verifier-quotes"]
check(svc["volumes"] == ["kk-artifacts:/artifacts:ro"] and svc["environment"]["KK_ARTIFACTS"] == "/artifacts"
      and "kk-artifacts" in vq["volumes"], "a worker that requires the artifact store reads it, read-only")
check("volumes" not in service("recall-index", manifest("recall-index"), "/x.env", "img")["services"]["module-recall-index"],
      "other workers get no volume")

check({"urn:kk:agent:evaluator", "urn:kk:agent:verifier-quotes"} <= module_agents(),
      "kk agent add knows the modules' agents, so it refuses their names")

print(f"D-55 sandbox policy: {ok} checks passed")

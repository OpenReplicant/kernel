"""decoder-compose checks (D-125): what encoder-compose recorded decodes to the same compose file, and a changed claim
changes the file.

  python modules/decoder-compose/tests/test_decode.py

Round trips: the encoder's fixture, the kernel's own deploy/compose.yaml, and any file named in COMPOSE_FILES
(colon-separated; Substrate's deploy/compose.yaml when the kernel is checked out inside it).
"""
import copy
import os
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "modules/encoder-compose"))
from decoder_compose.decode import KKT, claims_from_ops, dump, render  # noqa: E402
from encoder_compose.encode import PROPERTY, encode  # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)


def round_trip(text: str, project: str):
    ops, _, _ = encode(text, "0" * 64, project)
    claims = claims_from_ops(ops)
    t = f"urn:kk:service-template:{project}"
    return claims, t, yaml.safe_load(dump(render(text, claims, t)))


files = [("the fixture", ROOT / "modules/encoder-compose/tests/fixtures/compose.yaml"), ("the kernel's deploy/compose.yaml", ROOT / "deploy/compose.yaml")]
substrate = ROOT.parents[1] / "deploy/compose.yaml"
if substrate.exists() and (ROOT.parents[1] / "catalog").is_dir():
    files.append(("Substrate's deploy/compose.yaml", substrate))
files += [(f, Path(f)) for f in os.environ.get("COMPOSE_FILES", "").split(":") if f]
for label, f in files:
    text = f.read_text()
    _, _, back = round_trip(text, "p")
    check(f"{label} decodes to itself (as compose reads it)", back == yaml.safe_load(text))

text = (ROOT / "modules/encoder-compose/tests/fixtures/compose.yaml").read_text()
claims, t, _ = round_trip(text, "shop")


def edited(change):
    cs = copy.deepcopy(claims)
    change(cs)
    return yaml.safe_load(dump(render(text, cs, t)))


def prop(cs, node, name):
    return [c for c in cs if c["subject"] == f"urn:kk:node:{node}" and (c["qualifiers"] or {}).get(PROPERTY) == name]


def set_image(cs):
    prop(cs, "api", "image")[0]["object_value"] = {"@value": "ghcr.io/example/shop-api:1.5"}


out = edited(set_image)
check("a changed image claim changes the image, and nothing else in the service",
      out["services"]["api"]["image"] == "ghcr.io/example/shop-api:1.5"
      and {k: v for k, v in out["services"]["api"].items() if k != "image"} == {k: v for k, v in yaml.safe_load(text)["services"]["api"].items() if k != "image"})


def drop_dep(cs):
    cs[:] = [c for c in cs if not (c["subject"] == "urn:kk:node:worker" and c["predicate"] == KKT + "dependsOn" and c["object_entity"] == "urn:kk:node:api")]


check("a retracted dependency leaves the list", edited(drop_dep)["services"]["worker"]["depends_on"] == ["db"])


def add_profile(cs):
    cs.append({"subject": "urn:kk:node:db", "predicate": KKT + "propertyValue", "object_entity": None,
               "object_value": {"@value": "core"}, "qualifiers": {PROPERTY: "profile"}})


check("a new profile claim adds the profile", edited(add_profile)["services"]["db"].get("profiles") == ["core"])


def retract_member(cs):
    cs[:] = [c for c in cs if not (c["subject"] == "urn:kk:node:ghost" and c["predicate"] == KKT + "memberOf")]


check("a service no longer a member of the template is dropped", "ghost" not in edited(retract_member)["services"])


def new_member(cs):
    for p, o, v, q in ((KKT + "memberOf", t, None, {}), (KKT + "propertyValue", None, "docker.io/library/redis:7.4", {PROPERTY: "image"}),
                       (KKT + "dependsOn", "urn:kk:node:db", None, {})):
        cs.append({"subject": "urn:kk:node:cache", "predicate": p, "object_entity": o, "object_value": {"@value": v} if v else None, "qualifiers": q})


svc = edited(new_member)["services"].get("cache")
check("a new member is added with what its claims say", svc == {"image": "docker.io/library/redis:7.4", "depends_on": ["db"]}, svc)


def add_env(cs):
    cs.append({"subject": "urn:kk:node:db", "predicate": KKT + "propertyValue", "object_entity": None,
               "object_value": {"@value": "PGDATA"}, "qualifiers": {PROPERTY: "environment"}})


env = edited(add_env)["services"]["db"]["environment"]
check("an environment name added keeps the other values and passes the new one through", env == {"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:?set in .env}", "PGDATA": None}, env)
check("a dependency's condition survives decoding", yaml.safe_load(dump(render(text, claims, t)))["services"]["api"]["depends_on"] == {"db": {"condition": "service_healthy"}})

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)

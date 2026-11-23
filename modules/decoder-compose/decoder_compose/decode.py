"""A service template's claims -> its compose file again (D-125). Pure functions; PyYAML only.

encoder-compose keeps the file as the template's version (D-101) and states the fields people and agents change as
claims. Decoding starts from that version and applies the current claims, so the kernel's view wins where the two
differ, and everything the claims do not model (healthchecks, security options, top-level volumes, `x-` blocks) comes
from the version unchanged:

- a service is in the file when its node is `kkt:memberOf` the template: one no longer a member is dropped, a new
  member is added with what its claims say;
- image: the `image` property value (with no claim, the service has none);
- profiles, ports, volumes: when the claims hold the same values as the version, the version's list is kept as
  written; otherwise the version's items still claimed, in their order, then the new ones;
- environment: by name, the version's entries whose names are still claimed (with their values), then a new name
  as a pass-through (`NAME` with no value: compose takes it from the environment); values never come from claims;
- depends_on: the version's dependencies still claimed, with their conditions, then new ones.

Anchors and comments are not kept (PyYAML expands merges), so the output equals the input as compose reads it
(`yaml.safe_load` of both), not byte for byte.
"""
from __future__ import annotations

import copy
import json

import yaml

KKT = "https://w3id.org/knowledge-kernel/tosca#"
PROPERTY = KKT + "propertyName"


def template_iri(project: str) -> str:
    return f"urn:kk:service-template:{project}"


def value(c: dict):
    v = c.get("object_value")
    return v.get("@value") if isinstance(v, dict) else v


def as_text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def from_text(t: str):
    if t.startswith(("{", "[")):
        try:
            return json.loads(t)
        except ValueError:
            pass
    return t


def fields(claims: list[dict], template: str) -> tuple[list[str], dict[str, dict]]:
    """The member services (node names) and, per service, its claimed fields."""
    members, per = [], {}
    for c in claims:
        if c["predicate"] == KKT + "memberOf" and c.get("object_entity") == template:
            name = c["subject"].removeprefix("urn:kk:node:")
            if name not in per:
                members.append(name)
                per[name] = {"image": [], "profile": [], "port": [], "volume": [], "environment": [], "depends": []}
    for c in claims:
        name = c["subject"].removeprefix("urn:kk:node:")
        if name not in per:
            continue
        if c["predicate"] == KKT + "propertyValue":
            prop = (c.get("qualifiers") or {}).get(PROPERTY)
            if prop in per[name] and value(c) not in per[name][prop]:
                per[name][prop].append(value(c))
        elif c["predicate"] == KKT + "dependsOn" and c.get("object_entity"):
            dep = c["object_entity"].removeprefix("urn:kk:node:")
            if dep not in per[name]["depends"]:
                per[name]["depends"].append(dep)
    return members, per


def merge_list(old, claimed: list[str]):
    old = list(old or [])
    texts = [as_text(o) for o in old]
    if sorted(texts) == sorted(claimed):
        return old
    kept = [o for o, t in zip(old, texts) if t in claimed]
    return kept + [from_text(t) for t in claimed if t not in texts]


def merge_env(old, names: list[str]):
    if isinstance(old, dict):
        out = {k: v for k, v in old.items() if str(k) in names}
        out.update({n: None for n in names if n not in out})
        return out
    old = list(old or [])
    kept = [e for e in old if str(e).split("=", 1)[0] in names]
    have = {str(e).split("=", 1)[0] for e in kept}
    return kept + [n for n in names if n not in have]


def merge_deps(old, deps: list[str]):
    if isinstance(old, dict):
        out = {k: v for k, v in old.items() if k in deps}
        out.update({d: {"condition": "service_started"} for d in deps if d not in out})
        return out
    old = list(old or [])
    return [d for d in old if d in deps] + [d for d in deps if d not in old]


def render(version_text: str, claims: list[dict], template: str) -> dict:
    """The compose document: the version with the current claims applied."""
    data = yaml.safe_load(version_text) or {}
    old_services = data.get("services") or {}
    members, per = fields(claims, template)
    order = [n for n in old_services if n in per] + [n for n in members if n not in old_services]
    services = {}
    for name in order:
        svc, f = copy.deepcopy(old_services.get(name) or {}), per[name]
        if f["image"]:
            svc["image"] = f["image"][-1]
        else:
            svc.pop("image", None)
        for key, prop in (("profiles", "profile"), ("ports", "port"), ("volumes", "volume")):
            merged = merge_list(svc.get(key), f[prop])
            if merged:
                svc[key] = merged
            else:
                svc.pop(key, None)
        if f["environment"] or "environment" in svc:
            env = merge_env(svc.get("environment"), f["environment"])
            if env:
                svc["environment"] = env
            else:
                svc.pop("environment", None)
        if f["depends"] or "depends_on" in svc:
            deps = merge_deps(svc.get("depends_on"), f["depends"])
            if deps:
                svc["depends_on"] = deps
            else:
                svc.pop("depends_on", None)
        services[name] = svc
    out = {k: v for k, v in data.items() if k != "services"}
    out["services"] = services
    return out


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def claims_from_ops(ops: list[dict]) -> list[dict]:
    """encoder-compose's add_claim ops as the rows decoding reads (for tests and dry runs)."""
    return [{"subject": op["subject"], "predicate": op["predicate"], "object_entity": op["object"].get("entity"),
             "object_value": op["object"].get("value"), "qualifiers": op.get("qualifiers") or {}}
            for op in ops if op["op"] == "add_claim"]

"""A compose file -> the claims it states, in kk-tosca's terms (D-125). Pure functions; PyYAML only.

The file is the service template's version (D-101: an item's content is an artifact): `kkt:hasArtifact` links the
template to it, so a decoder can render the file again from the artifact and the current claims. The fields a person
or an agent changes in the kernel are claims, each citing the lines it was read from:

  the project                      urn:kk:service-template:<project>   rdf:type kkt:ServiceTemplate, rdfs:label,
                                                                       kkt:hasArtifact urn:kk:artifact:<sha256>
  services.<name>                  urn:kk:node:<name>                  rdf:type kkt:NodeTemplate, rdfs:label,
                                                                       kkt:memberOf the template
    image                          kkt:nodeType urn:kk:node-type:<image without tag or digest>
                                   kkt:propertyValue <image as written> {kkt:propertyName: image}
    profiles[]                     kkt:propertyValue {kkt:propertyName: profile}
    ports[], volumes[]             kkt:propertyValue {kkt:propertyName: port | volume} (long forms as JSON)
    environment                    kkt:propertyValue {kkt:propertyName: environment}: the variable's NAME only
    depends_on                     kkt:dependsOn urn:kk:node:<dependency>

Values are kept as written (`${IMAGE:-...}` stays an interpolation); environment values are never read, since a
compose file may hold a secret there. A service with neither image nor build is a finding. Node IRIs are the
names other modules use for the same part (the self-encoder, encoder-backstage's components), so what is said about
a service from anywhere meets.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

KKT = "https://w3id.org/knowledge-kernel/tosca#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
AGENT = "urn:kk:agent:encoder-compose"
SCOPE = "urn:kk:scope:default"
PROPERTY = KKT + "propertyName"


def template_iri(project: str) -> str:
    return f"urn:kk:service-template:{project}"


def node_iri(service: str) -> str:
    return f"urn:kk:node:{service}"


def node_type(image: str | None, service: str) -> str:
    """The image's repository, without tag or digest; a build-only service is a type of its own."""
    if not image:
        return f"urn:kk:node-type:{service}"
    repo = image.split("@", 1)[0]
    last = repo.rsplit("/", 1)
    if ":" in last[-1]:
        repo = repo[: len(repo) - len(last[-1]) + last[-1].index(":")]
    return f"urn:kk:node-type:{repo}"


def spans(text: str) -> tuple[dict, dict]:
    """The document and a map from a path of keys and list indexes to (start_line, end_line), 1-based."""
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        out: dict = {}

        def walk(n, path):
            end = n.end_mark.line + (1 if n.end_mark.column else 0)
            out[path] = (n.start_mark.line + 1, max(n.start_mark.line + 1, end))
            if isinstance(n, yaml.MappingNode):
                for k, v in n.value:
                    if isinstance(k, yaml.ScalarNode):
                        walk(v, path + (k.value,))
                        out[path + (k.value,)] = (k.start_mark.line + 1, out[path + (k.value,)][1])
            elif isinstance(n, yaml.SequenceNode):
                for i, v in enumerate(n.value):
                    walk(v, path + (i,))

        if node is not None:
            walk(node, ())
        data = loader.construct_document(node) if node is not None else {}
        return data or {}, out
    finally:
        loader.dispose()


def env_names(env) -> list[str]:
    if isinstance(env, dict):
        return [str(k) for k in env]
    return [str(e).split("=", 1)[0] for e in env or []]


class Builder:
    def __init__(self, sha: str):
        self.sha, self.ops, self.seen = sha, [], set()

    def claim(self, s, p, o, span, entity=True, q=None):
        key = (s, p, str(o), entity, tuple(sorted((q or {}).items())))
        if key in self.seen or o is None or o == "":
            return
        self.seen.add(key)
        op = {"op": "add_claim", "subject": s, "predicate": p,
              "object": {"entity": o} if entity else {"value": {"@value": str(o)}},
              "evidence_type": "deterministic",
              "references": [{"artifact": self.sha, "span": {"start_line": span[0], "end_line": span[1]}}]}
        if q:
            op["qualifiers"] = q
        self.ops.append(op)


def as_text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def encode(text: str, sha: str, project: str) -> tuple[list[dict], list[dict], dict]:
    """The add_claim ops a compose file states, the findings, and counts."""
    b, findings = Builder(sha), []
    data, sp = spans(text)
    whole = sp.get((), (1, max(1, len(text.splitlines()))))
    t = template_iri(project)
    b.claim(t, RDF_TYPE, KKT + "ServiceTemplate", whole)
    b.claim(t, RDFS_LABEL, data.get("name") or project, sp.get(("name",), whole), entity=False)
    b.claim(t, KKT + "hasArtifact", f"urn:kk:artifact:{sha}", whole)
    services = data.get("services") or {}
    for name, svc in services.items():
        svc = svc or {}
        n, here = node_iri(name), sp.get(("services", name), whole)

        def at(*path):
            return sp.get(("services", name) + path, here)

        b.claim(n, RDF_TYPE, KKT + "NodeTemplate", here)
        b.claim(n, RDFS_LABEL, name, here, entity=False)
        b.claim(n, KKT + "memberOf", t, here)
        image = svc.get("image")
        if not image and not svc.get("build"):
            findings.append({"finding": "no-image-or-build", "service": name, "line": here[0]})
        b.claim(n, KKT + "nodeType", node_type(image, name), at("image") if image else at("build"))
        if image:
            b.claim(n, KKT + "propertyValue", image, at("image"), entity=False, q={PROPERTY: "image"})
        for i, p in enumerate(svc.get("profiles") or []):
            b.claim(n, KKT + "propertyValue", p, at("profiles", i), entity=False, q={PROPERTY: "profile"})
        for field, prop in (("ports", "port"), ("volumes", "volume")):
            for i, v in enumerate(svc.get(field) or []):
                b.claim(n, KKT + "propertyValue", as_text(v), at(field, i), entity=False, q={PROPERTY: prop})
        for k in env_names(svc.get("environment")):
            b.claim(n, KKT + "propertyValue", k, at("environment", k) if isinstance(svc.get("environment"), dict)
                    else at("environment"), entity=False, q={PROPERTY: "environment"})
        deps = svc.get("depends_on") or []
        for dep in deps if isinstance(deps, list) else list(deps):
            if dep not in services:
                findings.append({"finding": "unknown-dependency", "service": name, "depends_on": dep, "line": at("depends_on")[0]})
            b.claim(n, KKT + "dependsOn", node_iri(dep), at("depends_on", dep) if isinstance(deps, dict) else at("depends_on"))
    return b.ops, findings, {"services": len(services)}


def key_prefix(project: str, rel: str) -> str:
    return f"compose:{project}:{rel}:"


def change_set(path: Path, project: str, rel: str, store: Path) -> tuple[dict, list[dict], dict]:
    """The file stored as an artifact, then its claims; keyed by project, path and content."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    store.mkdir(parents=True, exist_ok=True)
    if not (store / sha).exists():
        (store / sha).write_bytes(data)
    ops, findings, counts = encode(data.decode(), sha, project)
    cs = {"idempotency_key": f"{key_prefix(project, rel)}{sha[:16]}", "attributed_to": AGENT, "target_scope": SCOPE,
          "ops": [{"op": "add_artifact", "sha256": sha, "media_type": "application/yaml", "byte_size": len(data),
                   "location": f"file://{store / sha}", "source_uri": f"file:{rel}",
                   "retrieved_at": datetime.now(timezone.utc).isoformat()}, *ops]}
    return cs, findings, counts


def same(s, p, entity, value, qualifiers) -> str:
    return json.dumps([s, p, entity, value, qualifiers or {}], sort_keys=True, default=str)


def supersede(cs: dict, prior: list[dict]) -> None:
    """Deprecate `prior`: superseded by the same statement in `cs` when it states it again, else retracted
    (encoder-backstage's rule, D-41 b)."""
    new = {}
    for i, op in enumerate(cs["ops"]):
        if op["op"] == "add_claim":
            op["ref"] = f"c{i}"
            o = op["object"]
            new[same(op["subject"], op["predicate"], o.get("entity"), (o.get("value") or {}).get("@value"),
                     op.get("qualifiers"))] = op["ref"]
    for c in prior:
        by = new.get(same(c["subject"], c["predicate"], c["object_entity"],
                          (c["object_value"] or {}).get("@value"), c["qualifiers"]))
        cs["ops"].append({"op": "set_status", "claim": str(c["id"]), "status": "deprecated", "expect_status": c["status"],
                          **({"reason": "superseded", "replaced_by": by} if by else {"reason": "retracted"})})

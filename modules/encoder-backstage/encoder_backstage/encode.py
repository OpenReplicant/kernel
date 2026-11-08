"""A Backstage catalog file -> the claims it states, in Backstage's own terms (D-100). Pure functions; PyYAML only.

Each entity (one YAML document) becomes claims with the backstage pack's terms (ontology/backstage.ttl):
rdf:type its kind, rdfs:label (metadata.title, else its name), dcterms:description, and the relations Backstage's
own catalog processor derives from the spec, in its names:

  spec.owner                                  backstage:ownedBy
  spec.system, spec.domain, spec.subcomponentOf  backstage:partOf
  spec.providesApis / consumesApis            backstage:providesApi / consumesApi
  spec.dependsOn                              backstage:dependsOn
  spec.memberOf (User), spec.parent (Group)   backstage:memberOf, backstage:childOf
  spec.type, spec.lifecycle, metadata.tags    backstage:type, lifecycle, tag
  metadata.annotations                        backstage:annotation {backstage:annotationKey: the key}

Components and resources are `urn:kk:node:<name>`, the node names other modules use (the self-encoder's
services, Morphogen's cells, the metrics observer), so what is said about a part from anywhere meets; other
kinds are `urn:kk:<kind>:<name>`. Where kk-tosca or ORG name the same concept, the mappings pack says so.

Every claim is deterministic and references the stored file and the lines it was read from. What cannot be
read (an unknown kind, an entity without a name, a ref to an entity the file does not define) is returned as a
finding, never raised; a dangling ref is still claimed, as the file states it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

BS = "https://w3id.org/knowledge-kernel/backstage#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
DCT_DESCRIPTION = "http://purl.org/dc/terms/description"
AGENT = "urn:kk:agent:encoder-backstage"
SCOPE = "urn:kk:scope:default"

KINDS = {"component": "Component", "resource": "Resource", "system": "System", "domain": "Domain",
         "api": "API", "group": "Group", "user": "User"}
NODE = {"component", "resource"}
# Backstage's default kind for a ref in each field (descriptor format, "entity references"); dependsOn has none.
DEFAULT_KIND = {"owner": "group", "system": "system", "domain": "domain", "subcomponentOf": "component",
                "providesApis": "api", "consumesApis": "api", "memberOf": "group", "parent": "group"}


def iri(kind: str, name: str) -> str:
    return f"urn:kk:node:{name}" if kind in NODE else f"urn:kk:{kind}:{name}"


def parse_ref(ref, field: str) -> tuple[str | None, str]:
    """'[kind:][namespace/]name' -> (kind, name); the kind defaults by field."""
    ref = str(ref)
    if ":" in ref:
        kind, rest = ref.split(":", 1)
    else:
        kind, rest = DEFAULT_KIND.get(field), ref
    return (kind or "").lower() or None, rest.split("/")[-1]


def documents(text: str):
    """(data, spans) per YAML document; spans maps a path of keys and list indexes to (start_line, end_line)."""
    loader = yaml.SafeLoader(text)
    try:
        while loader.check_node():
            node = loader.get_node()
            if node is None:
                continue
            spans: dict = {}

            def walk(n, path):
                end = n.end_mark.line + (1 if n.end_mark.column else 0)   # column 0: ended on the line before
                spans[path] = (n.start_mark.line + 1, max(n.start_mark.line + 1, end))
                if isinstance(n, yaml.MappingNode):
                    for k, v in n.value:
                        walk(v, path + (k.value,))
                        spans[path + (k.value,)] = (k.start_mark.line + 1, spans[path + (k.value,)][1])
                elif isinstance(n, yaml.SequenceNode):
                    for i, v in enumerate(n.value):
                        walk(v, path + (i,))

            walk(node, ())
            yield loader.construct_document(node), spans
    finally:
        loader.dispose()


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


def encode(text: str, sha: str) -> tuple[list[dict], list[dict], dict]:
    """The add_claim ops a catalog file states, the findings, and the number of entities per kind."""
    b, findings, counts = Builder(sha), [], {}
    docs = [(d, sp) for d, sp in documents(text) if isinstance(d, dict)]
    defined = {(str(d.get("kind", "")).lower(), str((d.get("metadata") or {}).get("name", ""))) for d, _ in docs}
    for d, sp in docs:
        kind, md, spec = str(d.get("kind", "")).lower(), d.get("metadata") or {}, d.get("spec") or {}
        at = sp.get(("kind",), sp[()])
        if kind == "location":       # points at other files; nothing to say about the system
            continue
        if kind not in KINDS:
            findings.append({"finding": "unknown-kind", "kind": d.get("kind"), "line": at[0]})
            continue
        if not md.get("name"):
            findings.append({"finding": "no-name", "kind": d.get("kind"), "line": at[0]})
            continue
        counts[KINDS[kind]] = counts.get(KINDS[kind], 0) + 1
        s, here = iri(kind, str(md["name"])), sp[("metadata", "name")]

        def line(*path):
            return sp.get(path, here)

        def rel(predicate, field, value, path):
            k, n = parse_ref(value, field)
            if not k or (k, n) not in defined:
                findings.append({"finding": "dangling-ref", "entity": s, "field": field, "ref": str(value),
                                 "line": line(*path)[0]})
            b.claim(s, BS + predicate, iri(k or "component", n), line(*path))

        b.claim(s, RDF_TYPE, BS + KINDS[kind], here)
        b.claim(s, RDFS_LABEL, md.get("title") or md["name"], line("metadata", "title"), entity=False)
        b.claim(s, DCT_DESCRIPTION, md.get("description"), line("metadata", "description"), entity=False)
        for field in ("type", "lifecycle"):
            b.claim(s, BS + field, spec.get(field), line("spec", field), entity=False)
        for i, tag in enumerate(md.get("tags") or []):
            b.claim(s, BS + "tag", tag, line("metadata", "tags", i), entity=False)
        for key, value in (md.get("annotations") or {}).items():
            b.claim(s, BS + "annotation", value, line("metadata", "annotations", key), entity=False,
                    q={BS + "annotationKey": key})
        if spec.get("owner"):
            rel("ownedBy", "owner", spec["owner"], ("spec", "owner"))
        for field in ("system", "domain", "subcomponentOf"):
            if spec.get(field):
                rel("partOf", field, spec[field], ("spec", field))
        if spec.get("parent"):
            rel("childOf", "parent", spec["parent"], ("spec", "parent"))
        for field, predicate in (("providesApis", "providesApi"), ("consumesApis", "consumesApi"),
                                 ("dependsOn", "dependsOn"), ("memberOf", "memberOf")):
            for i, value in enumerate(spec.get(field) or []):
                rel(predicate, field, value, ("spec", field, i))
    return b.ops, findings, counts


def key_prefix(project: str, rel: str) -> str:
    return f"backstage:{project}:{rel}:"


def change_set(path: Path, project: str, rel: str, store: Path) -> tuple[dict, list[dict], dict]:
    """The file stored as an artifact, then its claims; keyed by project, path and content."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    store.mkdir(parents=True, exist_ok=True)
    if not (store / sha).exists():
        (store / sha).write_bytes(data)
    ops, findings, counts = encode(data.decode(), sha)
    cs = {"idempotency_key": f"{key_prefix(project, rel)}{sha[:16]}", "attributed_to": AGENT, "target_scope": SCOPE,
          "ops": [{"op": "add_artifact", "sha256": sha, "media_type": "application/yaml", "byte_size": len(data),
                   "location": f"file://{store / sha}", "source_uri": f"file:{rel}",
                   "retrieved_at": datetime.now(timezone.utc).isoformat()}, *ops]}
    return cs, findings, counts

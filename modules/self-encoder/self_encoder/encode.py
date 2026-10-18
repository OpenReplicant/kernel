"""Files -> change sets. Deterministic parsers only; every claim references its file artifact and
the line span it was read from ({"start_line", "end_line"}, 1-based).

  modules/*/module.yaml      kk:Module + kk:fills + kkt:hasRequirement + kkt:hasAffordance
  deploy/compose.yaml (+ demo/) kkt:NodeTemplate + kkt:nodeType + kkt:dependsOn + kkt:memberOf
  **/*.ttl                   owl:Ontology (packs) or a graph entity (shapes, fixtures)
  *.sql (schema, migrations) schema objects (dcterms:isPartOf the db node or the module)

and, in any repository (repo.py, M3-T2): package manifests (packages and their dependencies) and
Markdown documents (their headings).
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path

import rdflib
import yaml

from . import repo as anyrepo

AGENT = "urn:kk:agent:self-encoder"
SCOPE = "urn:kk:scope:default"
KINDS = {"encoder": "kk:Encoder", "processor": "kk:Processor", "decoder": "kk:Decoder",
         "actuator": "kk:Actuator", "interface": "kk:Interface", "agent": "kk:AgentKind",
         "control": "kk:Control", "pack": "kk:Pack"}
AFFORDANCES = {"action": "td:ActionAffordance", "property": "td:PropertyAffordance", "event": "td:EventAffordance"}
MEDIA = {".yaml": "application/yaml", ".yml": "application/yaml", ".ttl": "text/turtle", ".sql": "application/sql",
         ".md": "text/markdown", ".json": "application/json", ".toml": "application/toml"}
SERVICE_TEMPLATE = "urn:kk:service-template:knowledge-kernel"
SQL_OBJECT = re.compile(r"^\s*create\s+(?:or\s+replace\s+)?(schema|table|view|function|role|type)\s+"
                        r"(?:if\s+not\s+exists\s+)?([A-Za-z_][\w.]*)", re.I)


def list_files(repo: Path) -> list[str]:
    """The files the self-model is built from (relative paths, sorted)."""
    patterns = ["modules/*/module.yaml", "modules/*/migration.sql",
                "deploy/compose.yaml", "deploy/demo/compose.yaml", "kernel/schema/*.sql", "ontology/*.ttl", "shapes/**/*.ttl"]
    found = {str(p.relative_to(repo)) for pat in patterns for p in repo.glob(pat)} | set(anyrepo.list_repo_files(repo))
    return sorted(p for p in found if "/_template/" not in f"/{p}")


# ------------------------------------------------------------------ YAML with line numbers
def yaml_lines(text: str):
    """(data, spans): spans maps a path tuple (keys / list indexes) to (start_line, end_line)."""
    spans = {}

    def walk(node, path):
        # end_mark is just past the node: column 0 means the node ended on the previous line.
        end = node.end_mark.line + (1 if node.end_mark.column else 0)
        spans[path] = (node.start_mark.line + 1, max(node.start_mark.line + 1, end))
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, path + (k.value,))
                spans[path + (k.value,)] = (k.start_mark.line + 1, spans[path + (k.value,)][1])
        elif isinstance(node, yaml.SequenceNode):
            for i, v in enumerate(node.value):
                walk(v, path + (i,))

    node = yaml.compose(text)
    walk(node, ())
    return yaml.safe_load(text), spans


class Builder:
    """Collects ops for one file's change set."""

    def __init__(self, rel: str, data: bytes, artifacts_dir: Path, key: str = "self-encoder:"):
        self.rel, self.sha, self.key = rel, hashlib.sha256(data).hexdigest(), key
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        stored = artifacts_dir / self.sha
        if not stored.exists():
            stored.write_bytes(data)
        self.seen: set = set()
        self.ops = [{"op": "add_artifact", "sha256": self.sha, "media_type": MEDIA.get(Path(rel).suffix, "text/plain"),
                     "byte_size": len(data), "location": f"file://{artifacts_dir}/{self.sha}",
                     "source_uri": f"file:{rel}", "retrieved_at": datetime.now(timezone.utc).isoformat()}]

    def claim(self, s: str, p: str, o, span: tuple[int, int], qualifiers: dict | None = None) -> None:
        """o is an IRI for predicates in ENTITY_PREDICATES, otherwise a literal (or a JSON-LD value dict).
        A claim already made in this change set is not repeated."""
        key = (s, p, repr(o), repr(sorted((qualifiers or {}).items())))
        if key in self.seen:
            return
        self.seen.add(key)
        if p in ENTITY_PREDICATES:
            obj = {"entity": o}
        elif isinstance(o, dict):
            obj = {"value": o}
        elif isinstance(o, bool):
            obj = {"value": {"@value": o, "@type": "xsd:boolean"}}
        else:
            obj = {"value": {"@value": o}}
        op = {"op": "add_claim", "subject": s, "predicate": p, "object": obj, "evidence_type": "deterministic",
              "references": [{"artifact": self.sha, "span": {"start_line": span[0], "end_line": span[1]}}]}
        if qualifiers:
            op["qualifiers"] = qualifiers
        self.ops.append(op)

    def change_set(self) -> dict:
        return {"idempotency_key": f"{self.key}{self.rel}:{self.sha[:16]}", "attributed_to": AGENT,
                "target_scope": SCOPE, "ops": self.ops}


ENTITY_PREDICATES = {"rdf:type", "kk:moduleKind", "dcterms:license", "kk:runsAs", "kk:fills",
                     "kkt:hasRequirement", "kkt:hasAffordance", "kkt:memberOf",
                     "kkt:nodeType", "kkt:dependsOn", "kkt:hasArtifact", "dcterms:isPartOf", "dcterms:hasPart",
                     anyrepo.REQUIRES, anyrepo.SUGGESTS}


# ------------------------------------------------------------------ encoders per file kind
def encode_module(b: Builder, text: str, module_ids: dict) -> None:
    m, sp = yaml_lines(text)
    s = m["id"]
    b.claim(s, "rdf:type", "kk:Module", sp[("id",)])
    b.claim(s, "rdfs:label", m["name"], sp[("name",)])
    b.claim(s, "owl:versionInfo", m["version"], sp[("version",)])
    b.claim(s, "kk:moduleKind", KINDS[m["kind"]], sp[("kind",)])
    b.claim(s, "dcterms:license", f"https://spdx.org/licenses/{m['license']}", sp[("license",)])
    b.claim(s, "kk:runsAs", m["agent"]["iri"], sp[("agent", "iri")])
    b.claim(s, "kk:protected", bool(m.get("protected", False)), sp.get(("protected",), sp[("id",)]))
    if m.get("description"):
        b.claim(s, "rdfs:comment", m["description"], sp[("description",)])
    for i, slot in enumerate(m.get("slots", [])):
        b.claim(s, "kk:fills", f"urn:kk:slot:{slot}", sp[("slots", i)])
    for i, req in enumerate(m.get("requires", [])):
        if "slot" in req:
            b.claim(s, "kkt:hasRequirement", f"urn:kk:slot:{req['slot']}", sp[("requires", i)])
    for i, itf in enumerate(m.get("interfaces", [])):
        a = f"{s}/affordance/{itf['name']}"
        b.claim(s, "kkt:hasAffordance", a, sp[("interfaces", i)])
        b.claim(a, "rdf:type", AFFORDANCES[itf.get("affordance", "action")], sp[("interfaces", i)])
        b.claim(a, "rdfs:label", itf["name"], sp[("interfaces", i, "name")])


def encode_compose(b: Builder, text: str, module_ids: dict) -> None:
    d, sp = yaml_lines(text)
    b.claim(SERVICE_TEMPLATE, "rdf:type", "kkt:ServiceTemplate", sp[("name",)])
    b.claim(SERVICE_TEMPLATE, "rdfs:label", d["name"], sp[("name",)])
    for name, svc in d["services"].items():
        n = f"urn:kk:node:{name}"
        span = sp[("services", name)]
        b.claim(n, "rdf:type", "kkt:NodeTemplate", span)
        b.claim(n, "rdfs:label", name, span)
        b.claim(n, "kkt:memberOf", SERVICE_TEMPLATE, span)
        build = svc.get("build")
        ref = build if isinstance(build, str) else (build or {}).get("dockerfile", "")
        mod = re.search(r"modules/([^/]+)", ref or "")
        node_type = module_ids.get(mod.group(1)) if mod else None
        b.claim(n, "kkt:nodeType", node_type or f"urn:kk:node-type:{name}",
                sp.get(("services", name, "build"), sp.get(("services", name, "image"), span)))
        deps = svc.get("depends_on", [])
        for dep in deps if isinstance(deps, list) else deps.keys():
            b.claim(n, "kkt:dependsOn", f"urn:kk:node:{dep}", sp[("services", name, "depends_on")])
        for p in svc.get("profiles", []):
            b.claim(n, "kkt:propertyValue", p, sp[("services", name, "profiles")], {"kkt:propertyName": "profile"})


def encode_ttl(b: Builder, text: str, module_ids: dict) -> None:
    g = rdflib.Graph().parse(data=text, format="turtle")
    lines = text.splitlines()

    def line_of(needle: str) -> tuple[int, int]:
        for i, line in enumerate(lines, 1):
            if needle in line:
                return (i, i)
        return (1, len(lines))

    onts = list(g.subjects(rdflib.RDF.type, rdflib.OWL.Ontology))
    for o in onts:
        span = line_of(f"<{o}>")
        b.claim(str(o), "rdf:type", "owl:Ontology", span)
        for label in g.objects(o, rdflib.RDFS.label):
            b.claim(str(o), "rdfs:label", str(label), span)
        for v in g.objects(o, rdflib.OWL.versionInfo):
            b.claim(str(o), "owl:versionInfo", str(v), span)
    if onts and not list(g.subjects(rdflib.RDF.type, rdflib.SH.NodeShape)):
        return
    graph = f"urn:kk:graph:{b.rel}"
    b.claim(graph, "rdfs:label", b.rel, (1, len(lines)))
    shapes = sorted(g.subjects(rdflib.RDF.type, rdflib.SH.NodeShape))
    if shapes:
        b.claim(graph, "rdf:type", "sh:ShapesGraph", (1, len(lines)))
    for shape in shapes:
        b.claim(graph, "dcterms:hasPart", str(shape), line_of(g.namespace_manager.normalizeUri(shape)))


def encode_sql(b: Builder, text: str, module_ids: dict) -> None:
    mod = re.match(r"modules/([^/]+)/", b.rel)
    owner = module_ids.get(mod.group(1)) if mod else "urn:kk:node:db"
    b.claim(owner, "kkt:hasArtifact", f"urn:kk:artifact:{b.sha}", (1, max(1, len(text.splitlines()))))
    seen = set()
    for i, line in enumerate(text.splitlines(), 1):
        m = SQL_OBJECT.match(line)
        if not m:
            continue
        kind, name = m.group(1).lower(), m.group(2).lower()
        obj = f"urn:kk:db-object:{kind}:{name}"
        if obj in seen:
            continue
        seen.add(obj)
        b.claim(obj, "rdfs:label", f"{kind} {name}", (i, i))
        b.claim(obj, "dcterms:isPartOf", owner, (i, i))


def key_prefix(repo: Path, name: str | None = None) -> str:
    """Change sets of one repository: 'self-encoder:<name>:<path>:<sha16>' (D-69), so two repositories
    with the same file never share a key, and a re-scan supersedes only its own repository's claims."""
    return f"self-encoder:{quote(anyrepo.repo_name(repo, name), safe='')}:"


def encode_file(repo: Path, rel: str, artifacts_dir: Path, name: str | None = None) -> dict:
    data = (repo / rel).read_bytes()
    text = data.decode("utf-8", errors="replace")
    b = Builder(rel, data, artifacts_dir, key_prefix(repo, name))
    if anyrepo.wanted(rel):
        anyrepo.encode(b, repo, rel, text, name)
        return b.change_set()
    module_ids = {p.parent.name: yaml.safe_load(p.read_text())["id"] for p in repo.glob("modules/*/module.yaml")}
    if rel.endswith("/module.yaml"):
        encode_module(b, text, module_ids)
    elif rel.endswith("compose.yaml"):
        encode_compose(b, text, module_ids)
    elif rel.endswith(".ttl"):
        encode_ttl(b, text, module_ids)
    elif rel.endswith(".sql"):
        encode_sql(b, text, module_ids)
    else:
        raise ValueError(f"no encoder for {rel}")
    return b.change_set()

"""Validation logic, no I/O: a change set (+ context type triples) -> (ok, report).

1. JSON Schema: kernel/schemas/change_set.schema.json.
2. RDF form: each add_claim becomes an rdf:Statement node in standard terms (PROV, DCTerms, Web
   Annotation; shapes/kernel.shacl.ttl) plus its content triple, so both the claim shapes and the
   content shapes (module, slot, and pack shapes in shapes/*.shacl.ttl) apply.
3. pySHACL with the kernel shapes. Only violations whose focus node is in the change set count;
   context triples (types of entities the change set points at) are there to satisfy sh:class.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from pyshacl import validate as shacl_validate
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, SH, XSD

VERSION = "0.3.1"
ROOT = Path(os.environ.get("KK_ROOT") or Path(__file__).resolve().parents[3])
SCHEMA = json.loads((ROOT / "kernel/schemas/change_set.schema.json").read_text())
SHAPES = Graph()
for _f in sorted((ROOT / "shapes").glob("*.shacl.ttl")):
    SHAPES.parse(_f)
KK = Namespace("https://w3id.org/knowledge-kernel/ns#")
OA = Namespace("http://www.w3.org/ns/oa#")

# Same prefixes as kernel.prefix (kernel/schema/002_bootstrap.sql).
PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "prov": "http://www.w3.org/ns/prov#",
    "pplan": "http://purl.org/net/p-plan#",
    "oa": "http://www.w3.org/ns/oa#",
    "sh": "http://www.w3.org/ns/shacl#",
    "td": "https://www.w3.org/2019/wot/td#",
    "kk": "https://w3id.org/knowledge-kernel/ns#",
    "kkt": "https://w3id.org/knowledge-kernel/tosca#",
    "kkp": "https://w3id.org/knowledge-kernel/profile#",
}


def _prepared_queries() -> None:
    """pySHACL runs each SPARQL-based constraint once per focus node, as a query string, and rdflib parses
    the string every time: 70% of validating a large change set (M3-T4). Translate each distinct query
    once and reuse it with each node's bindings, as rdflib's prepared queries are meant to be used."""
    from functools import lru_cache

    from rdflib.plugins.sparql import processor

    @lru_cache(maxsize=256)
    def prepared(text: str, base, ns: tuple):
        return processor.translateQuery(processor.parseQuery(text), base, dict(ns))

    query = processor.SPARQLProcessor.query

    def cached(self, strOrQuery, initBindings=None, initNs=None, base=None, DEBUG=False):
        if isinstance(strOrQuery, str):
            strOrQuery = prepared(strOrQuery, base, tuple(sorted((initNs or {}).items())))
        return query(self, strOrQuery, initBindings, initNs, base, DEBUG)
    processor.SPARQLProcessor.query = cached


_prepared_queries()
EVIDENCE = {name: URIRef(f"urn:kk:evidence:{name}")
            for name in ("deterministic", "extracted", "inferred", "observed", "testimony")}


def expand(term: str) -> str:
    prefix, sep, local = term.partition(":")
    if sep and prefix in PREFIXES and not local.startswith("//"):
        return PREFIXES[prefix] + local
    return term


def entity_objects(cs: dict) -> set[str]:
    """IRIs a change set points at, whose types the caller should supply as context."""
    return {expand(op["object"]["entity"]) for op in cs.get("ops", [])
            if op.get("op") == "add_claim" and "entity" in op.get("object", {})}


def to_rdf(cs: dict) -> tuple[Graph, set]:
    g = Graph()
    focus = set()
    base = f"urn:kk:change-set:{cs.get('id') or cs['idempotency_key']}"
    refs = {}
    now = Literal(datetime.now(timezone.utc).isoformat(), datatype=XSD.dateTime)
    for i, op in enumerate(cs["ops"]):
        if op["op"] != "add_claim":
            continue
        c = URIRef(f"{base}#op{i}")
        refs[op.get("ref", "")] = c
        s, p = URIRef(expand(op["subject"])), URIRef(expand(op["predicate"]))
        if "entity" in op["object"]:
            o = URIRef(expand(op["object"]["entity"]))
        else:
            v = op["object"]["value"]
            o = Literal(v["@value"], lang=v.get("@language"),
                        datatype=URIRef(expand(v["@type"])) if "@type" in v else None)
        g.add((s, p, o))
        focus |= {c, s}
        for t in [(c, RDF.type, RDF.Statement), (c, RDF.subject, s), (c, RDF.predicate, p), (c, RDF.object, o),
                  (c, PROV.wasAttributedTo, URIRef(expand(cs["attributed_to"]))),
                  (c, PROV.wasGeneratedBy, URIRef(f"urn:kk:run:{cs['generated_by']}")),
                  (c, DCTERMS.accessRights, URIRef(expand(cs["target_scope"]))),
                  (c, DCTERMS.type, EVIDENCE[op["evidence_type"]]),
                  (c, PROV.generatedAtTime, now)]:
            g.add(t)
        for r in op.get("references", []):
            src = BNode()
            g.add((c, DCTERMS.source, src))
            g.add((src, RDF.type, OA.SpecificResource))
            target = r.get("source_uri") or f"urn:kk:artifact:{r['artifact']}"
            g.add((src, OA.hasSource, URIRef(target)))
            if r.get("quote"):
                sel = BNode()
                g.add((src, OA.hasSelector, sel))
                g.add((sel, RDF.type, OA.TextQuoteSelector))
                g.add((sel, OA.exact, Literal(r["quote"])))
        for d in op.get("derived_from", []):
            g.add((c, PROV.wasDerivedFrom, refs.get(d) or URIRef(f"urn:kk:claim:{d}")))
    return g, focus


def validate(cs: dict, context: list[tuple[str, str, str]] = ()) -> tuple[bool, dict]:
    """cs: the change set (schema fields; `id` optional). context: (subject, predicate, object) IRI triples."""
    report = {"validator": f"urn:kk:module:validator-shacl@{VERSION}", "schema_errors": [], "shape_violations": []}
    body = {k: v for k, v in cs.items() if k in SCHEMA["properties"]}
    errors = sorted(jsonschema.Draft202012Validator(SCHEMA).iter_errors(body), key=lambda e: list(e.path))
    if errors:
        report["schema_errors"] = [{"path": "/".join(map(str, e.path)), "message": e.message[:300]}
                                   for e in errors]
        return False, report

    data, focus = to_rdf(cs)
    for s, p, o in context:
        data.add((URIRef(expand(s)), URIRef(expand(p)), URIRef(expand(o))))
    _, results, _ = shacl_validate(data, shacl_graph=SHAPES, advanced=True, allow_warnings=False)
    for r in results.subjects(RDF.type, SH.ValidationResult):
        node = results.value(r, SH.focusNode)
        if node not in focus:
            continue
        path = results.value(r, SH.resultPath)
        report["shape_violations"].append({
            "focus": str(node),
            "path": str(path) if isinstance(path, URIRef) else None,
            "message": str(results.value(r, SH.resultMessage) or ""),
        })
    report["shape_violations"].sort(key=lambda v: (v["focus"], v["path"] or "", v["message"]))
    return not report["shape_violations"], report

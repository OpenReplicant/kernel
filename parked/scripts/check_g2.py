"""Gate G2 (self-model complete), read through the API:
every module manifest, chain, compose service and .ttl file has claims, and the modeled modules,
slots, chains and steps conform to the kernel shapes (every kk:Module fills at least one slot).
"0 claims without a reference" and artifact coverage are checked in SQL by scripts/smoke_stack.sh.

Env: KK_URL (default http://127.0.0.1:8080/api), KK_TOKEN (any agent cleared for vocab and default).
Needs: pyyaml, rdflib, pyshacl.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import rdflib
import yaml
from pyshacl import validate
from rdflib.namespace import SH

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("KK_URL", "http://127.0.0.1:8080/api")
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
KK = "https://w3id.org/knowledge-kernel/ns#"
KKT = "https://w3id.org/knowledge-kernel/tosca#"


PREFIXES = {"xsd": "http://www.w3.org/2001/XMLSchema#", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}


def expand(term: str) -> str:
    prefix, _, local = term.partition(":")
    return PREFIXES[prefix] + local if prefix in PREFIXES else term


def claims():
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"select": "subject,predicate,object_entity,object_value,status",
                                    "status": "neq.deprecated", "order": "id", "limit": 1000, "offset": offset})
        req = urllib.request.Request(f"{URL}/claims?{q}", headers={"Authorization": f"Bearer {os.environ['KK_TOKEN']}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def main() -> int:
    rows = claims()
    typed = {(r["subject"], r["object_entity"]) for r in rows if r["predicate"] == RDF_TYPE}
    subjects = {r["subject"] for r in rows}
    missing = []
    for p in sorted(ROOT.glob("modules/*/module.yaml")):
        if p.parent.name.startswith("_"):
            continue
        mid = yaml.safe_load(p.read_text())["id"]
        if (mid, KK + "Module") not in typed:
            missing.append(f"module {mid} ({p.relative_to(ROOT)})")
    for p in sorted(ROOT.glob("modules/*/chains/*.yaml")):
        if p.parts[-3].startswith("_"):
            continue
        cid = yaml.safe_load(p.read_text())["chain"]
        if (cid, KK + "Chain") not in typed:
            missing.append(f"chain {cid} ({p.relative_to(ROOT)})")
    for svc in yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())["services"]:
        if (f"urn:kk:node:{svc}", KKT + "NodeTemplate") not in typed:
            missing.append(f"compose service {svc}")
    for p in sorted(list(ROOT.glob("ontology/*.ttl")) + list(ROOT.glob("shapes/**/*.ttl"))):
        rel = str(p.relative_to(ROOT))
        g = rdflib.Graph().parse(p)
        onts = {str(o) for o in g.subjects(rdflib.RDF.type, rdflib.OWL.Ontology)}
        if not (onts & subjects) and f"urn:kk:graph:{rel}" not in subjects:
            missing.append(f"ttl file {rel}")

    data = rdflib.Graph()
    for r in rows:
        s, p = rdflib.URIRef(r["subject"]), rdflib.URIRef(r["predicate"])
        if r["object_entity"]:
            o = rdflib.URIRef(r["object_entity"])
        elif r["object_value"] is not None:
            v = r["object_value"]
            dt = v.get("@type")
            o = rdflib.Literal(v["@value"], lang=v.get("@language"), datatype=rdflib.URIRef(expand(dt)) if dt else None)
        else:
            continue                                     # redacted
        data.add((s, p, o))
    shapes = rdflib.Graph().parse(ROOT / "shapes/kernel.shacl.ttl")
    conforms, report, _ = validate(data, shacl_graph=shapes, advanced=True)
    violations = [f"{report.value(v, SH.focusNode)}: {report.value(v, SH.resultMessage)}"
                  for v in report.subjects(rdflib.RDF.type, SH.ValidationResult)]

    modules = sum(1 for s, t in typed if t == KK + "Module")
    chains = sum(1 for s, t in typed if t == KK + "Chain")
    nodes = sum(1 for s, t in typed if t == KKT + "NodeTemplate")
    print(f"G2: {len(rows)} claims; {modules} modules, {chains} chains, {nodes} node templates")
    for m in missing:
        print("  missing:", m)
    for v in violations[:20]:
        print("  shape violation:", v)
    ok = not missing and not violations
    print("G2 self-model", "complete" if ok else "INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

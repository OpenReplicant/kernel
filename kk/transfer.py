"""kk export / kk import: every claim with its provenance as N-Quads, one named graph per scope (T19, D-45).

  kk export [--scope IRI]... [--out FILE]     admin login (KK_ADMIN_URL); default stdout
  kk import FILE                              admin login; into a kernel without those claims

A claim is an rdf:Statement (IRI urn:kk:claim:<id>) in the graph of its scope, in standard terms:
rdf:subject/predicate/object; prov:wasAttributedTo, prov:wasGeneratedBy (urn:kk:run:<id>),
prov:generatedAtTime (recorded time), prov:wasDerivedFrom; dcterms:accessRights (scope), dcterms:type
(urn:kk:evidence:<type>); schema:validFrom/validThrough; qualifiers as <key> "<json>"^^rdf:JSON;
references as dcterms:source [oa:hasSource <artifact>; dcterms:source <uri>; oa:exact, oa:prefix,
oa:suffix; oa:hasSelector "<span json>"^^rdf:JSON]; the current status (when not normal) as adms:status
urn:kk:status:<s>, skos:changeNote <reason>, dcterms:isReplacedBy; the latest belief per model as
dqv:hasQualityMeasurement [dqv:isMeasurementOf <model>; dqv:value]. Artifacts, agents, runs and scopes
are described too. Redacted claims are left out (their content is gone).

Import keeps every claim's agent, scope, evidence, validity, qualifiers, references, derivations and
current status: each original run becomes one change set installed as kk_owner (kernel.install_ops)
and attributed to the original agent; agents and scopes missing in the target are created (agents
without a login). New claim ids and recorded times (when this kernel learned them); status changes are
attributed to the kernel agent with the original reason; beliefs are recomputed by the belief model.
Merging: a claim already present (same statement, conditions, validity, agent and scope) is not added
again, so importing into a kernel that has the bootstrap or the packs, or re-importing a file, changes
nothing for those claims.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from psycopg.types.json import Jsonb
from rdflib import BNode, Dataset, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, RDFS, SKOS, XSD

OA = Namespace("http://www.w3.org/ns/oa#")
DQV = Namespace("http://www.w3.org/ns/dqv#")
ADMS = Namespace("http://www.w3.org/ns/adms#")
SCHEMA = Namespace("https://schema.org/")
META = URIRef("urn:kk:graph:meta")
KINDS = {"person": PROV.Person, "software": PROV.SoftwareAgent, "organization": PROV.Organization}
KNOWN = {RDF.type, RDF.subject, RDF.predicate, RDF.object, PROV.wasAttributedTo, PROV.wasGeneratedBy,
         PROV.generatedAtTime, PROV.wasDerivedFrom, DCTERMS.accessRights, DCTERMS.type, SCHEMA.validFrom,
         SCHEMA.validThrough, DCTERMS.source, ADMS.status, SKOS.changeNote, DCTERMS.isReplacedBy,
         DQV.hasQualityMeasurement}
NATIVE = {XSD.integer: int, XSD.double: float, XSD.boolean: bool}


def claim_iri(i) -> URIRef:
    return URIRef(f"urn:kk:claim:{i}")


def to_literal(v: dict) -> Literal:
    """JSON-LD value object -> RDF literal."""
    value = v.get("@value")
    if "@language" in v:
        return Literal(str(value), lang=v["@language"])
    if "@type" in v:
        return Literal(str(value), datatype=URIRef(v["@type"]))
    return Literal(value)  # str -> plain; int/float/bool -> xsd typed


def from_literal(lit: Literal) -> dict:
    if lit.language:
        return {"@value": str(lit), "@language": lit.language}
    if lit.datatype in NATIVE:
        return {"@value": lit.toPython()}
    if lit.datatype and lit.datatype != XSD.string:
        return {"@value": str(lit), "@type": str(lit.datatype)}
    return {"@value": str(lit)}


def json_literal(value) -> Literal:
    return Literal(json.dumps(value, sort_keys=True), datatype=RDF.JSON)


# ---------------------------------------------------------------- export
def export(conn, scopes: list[str] | None = None) -> Dataset:
    ds = Dataset()
    meta = ds.graph(META)
    prefixes = dict(conn.execute("select prefix, namespace from kernel.prefix").fetchall())

    def expand(term: str) -> str:
        p, sep, local = term.partition(":")
        return prefixes[p] + local if sep and p in prefixes and not local.startswith("//") else term

    q = lambda s, *a: conn.execute(s, a).fetchall()  # noqa: E731
    only = "and c.scope = any(%s)" if scopes else ""
    args = (scopes,) if scopes else ()
    for iri, label in q("select iri, label from kernel.scope order by iri"):
        if not scopes or iri in scopes:
            meta.add((URIRef(iri), RDF.type, DCTERMS.RightsStatement))
            meta.add((URIRef(iri), RDFS.label, Literal(label)))
    for iri, kind, label in q("select iri, kind, label from kernel.agent order by iri"):
        meta.add((URIRef(iri), RDF.type, KINDS[kind]))
        meta.add((URIRef(iri), RDFS.label, Literal(label)))
    for rid, agent, module, started in q(f"""select distinct r.id, r.agent, r.module, r.started_at from kernel.run r
            join kernel.claim c on c.generated_by = r.id where c.redacted_at is null {only}""", *args):
        meta.add((URIRef(f"urn:kk:run:{rid}"), RDF.type, PROV.Activity))
        meta.add((URIRef(f"urn:kk:run:{rid}"), PROV.wasAssociatedWith, URIRef(agent)))
        meta.add((URIRef(f"urn:kk:run:{rid}"), PROV.startedAtTime, Literal(started)))
        if module:
            meta.add((URIRef(f"urn:kk:run:{rid}"), PROV.used, URIRef(module)))
    for sha, mt, size, loc, scope, src, retrieved in q(f"""select a.sha256, a.media_type, a.byte_size, a.location,
            a.scope, a.source_uri, a.retrieved_at from kernel.artifact a where a.redacted_at is null
            {"and a.scope = any(%s)" if scopes else ""} order by a.sha256""", *args):
        g, a = ds.graph(URIRef(scope)), URIRef(f"urn:kk:artifact:{sha}")
        g.add((a, RDF.type, PROV.Entity))
        g.add((a, DCTERMS.format, Literal(mt)))
        g.add((a, DCTERMS.extent, Literal(size)))
        g.add((a, PROV.atLocation, Literal(loc, datatype=XSD.anyURI)))
        if src:
            g.add((a, DCTERMS.source, Literal(src, datatype=XSD.anyURI)))
        if retrieved:
            g.add((a, DCTERMS.date, Literal(retrieved)))

    claims = q(f"""select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.valid_from,
            c.valid_to, c.scope, c.evidence_type, c.attributed_to, c.generated_by, c.recorded_at,
            (select row(s.status, s.reason, s.replaced_by) from kernel.claim_status s where s.claim_id = c.id
              order by s.recorded_at desc limit 1)
            from kernel.claim c where c.redacted_at is null {only} order by c.recorded_at, c.id""", *args)
    kept = {str(c[0]) for c in claims}
    refs, derived, beliefs = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in q("select claim_id, artifact, source_uri, quote, quote_prefix, quote_suffix, span from kernel.claim_reference"):
        refs[str(r[0])].append(r[1:])
    for cid, src in q("select claim_id, derived_from from kernel.claim_derivation"):
        derived[str(cid)].append(str(src))
    for cid, model, value in q("""select distinct on (claim_id, model) claim_id, model, value from kernel.belief
                                  order by claim_id, model, computed_at desc"""):
        beliefs[str(cid)].append((model, value))

    for (cid, s, p, o_ent, o_val, quals, vf, vt, scope, ev, agent, run, rec, st) in claims:
        cid = str(cid)
        g, c = ds.graph(URIRef(scope)), claim_iri(cid)
        g.add((c, RDF.type, RDF.Statement))
        g.add((c, RDF.subject, URIRef(s)))
        g.add((c, RDF.predicate, URIRef(p)))
        g.add((c, RDF.object, URIRef(o_ent) if o_ent else to_literal(o_val)))
        g.add((c, PROV.wasAttributedTo, URIRef(agent)))
        g.add((c, PROV.wasGeneratedBy, URIRef(f"urn:kk:run:{run}")))
        g.add((c, PROV.generatedAtTime, Literal(rec)))
        g.add((c, DCTERMS.accessRights, URIRef(scope)))
        g.add((c, DCTERMS.type, URIRef(f"urn:kk:evidence:{ev}")))
        if vf:
            g.add((c, SCHEMA.validFrom, Literal(vf)))
        if vt:
            g.add((c, SCHEMA.validThrough, Literal(vt)))
        for key, value in sorted((quals or {}).items()):
            g.add((c, URIRef(expand(key)), json_literal(value)))
        for art, uri, quote, pre, suf, span in refs.get(cid, []):
            r = BNode()
            g.add((c, DCTERMS.source, r))
            if art:
                g.add((r, OA.hasSource, URIRef(f"urn:kk:artifact:{art}")))
            if uri:
                g.add((r, DCTERMS.source, Literal(uri, datatype=XSD.anyURI)))
            for pred, val in ((OA.exact, quote), (OA.prefix, pre), (OA.suffix, suf)):
                if val is not None:
                    g.add((r, pred, Literal(val)))
            if span is not None:
                g.add((r, OA.hasSelector, json_literal(span)))
        for src in derived.get(cid, []):
            if src in kept:
                g.add((c, PROV.wasDerivedFrom, claim_iri(src)))
        if st and st[0] != "normal":
            g.add((c, ADMS.status, URIRef(f"urn:kk:status:{st[0]}")))
            if st[1]:
                g.add((c, SKOS.changeNote, Literal(st[1])))
            if st[2] and str(st[2]) in kept:
                g.add((c, DCTERMS.isReplacedBy, claim_iri(st[2])))
        for model, value in beliefs.get(cid, []):
            m = BNode()
            g.add((c, DQV.hasQualityMeasurement, m))
            g.add((m, DQV.isMeasurementOf, URIRef(model)))
            g.add((m, DQV.value, Literal(float(value))))
    return ds


# ---------------------------------------------------------------- import
def read(ds: Dataset) -> tuple[dict, dict, list[dict], dict]:
    """agents, scopes, claims (in recorded order), artifacts from an exported dataset."""
    meta = ds.graph(META)
    agents = {str(a): (str(meta.value(a, RDFS.label) or a),
                       next((k for k, v in KINDS.items() if (a, RDF.type, v) in meta), "software"))
              for a in set(meta.subjects(PROV.wasAssociatedWith, None)) | set(meta.objects(None, PROV.wasAssociatedWith))
              | {s for k in KINDS.values() for s in meta.subjects(RDF.type, k)}}
    scopes = {str(s): str(meta.value(s, RDFS.label) or s) for s in meta.subjects(RDF.type, DCTERMS.RightsStatement)}
    claims, artifacts = [], {}
    for g in ds.graphs():
        if g.identifier == META:
            continue
        for a in g.subjects(RDF.type, PROV.Entity):
            src = g.value(a, DCTERMS.source)
            artifacts[str(a).removeprefix("urn:kk:artifact:")] = {
                "op": "add_artifact", "sha256": str(a).removeprefix("urn:kk:artifact:"),
                "media_type": str(g.value(a, DCTERMS.format)), "byte_size": int(g.value(a, DCTERMS.extent)),
                "location": str(g.value(a, PROV.atLocation)), "scope": str(g.identifier),
                **({"source_uri": str(src)} if src is not None else {})}
        for c in g.subjects(RDF.type, RDF.Statement):
            o = g.value(c, RDF.object)
            refs = []
            for r in g.objects(c, DCTERMS.source):
                ref = {}
                if (art := g.value(r, OA.hasSource)) is not None:
                    ref["artifact"] = str(art).removeprefix("urn:kk:artifact:")
                if (uri := g.value(r, DCTERMS.source)) is not None:
                    ref["source_uri"] = str(uri)
                for key, pred in (("quote", OA.exact), ("quote_prefix", OA.prefix), ("quote_suffix", OA.suffix)):
                    if (v := g.value(r, pred)) is not None:
                        ref[key] = str(v)
                if (span := g.value(r, OA.hasSelector)) is not None:
                    ref["span"] = json.loads(str(span))
                refs.append(ref)
            status = g.value(c, ADMS.status)
            claims.append({
                "old": str(c).removeprefix("urn:kk:claim:"), "scope": str(g.identifier),
                "agent": str(g.value(c, PROV.wasAttributedTo)), "run": str(g.value(c, PROV.wasGeneratedBy)),
                "recorded": g.value(c, PROV.generatedAtTime).toPython(),
                "op": {"op": "add_claim", "ref": str(c).removeprefix("urn:kk:claim:"),
                       "subject": str(g.value(c, RDF.subject)), "predicate": str(g.value(c, RDF.predicate)),
                       "object": {"entity": str(o)} if isinstance(o, URIRef) else {"value": from_literal(o)},
                       "evidence_type": str(g.value(c, DCTERMS.type)).removeprefix("urn:kk:evidence:"),
                       "references": sorted(refs, key=lambda r: json.dumps(r, sort_keys=True)),
                       **({"qualifiers": q} if (q := {str(p): json.loads(str(v)) for p, v in g.predicate_objects(c)
                                                    if p not in KNOWN and isinstance(v, Literal)
                                                    and v.datatype == RDF.JSON}) else {}),
                       **({"valid_from": str(v)} if (v := g.value(c, SCHEMA.validFrom)) is not None else {}),
                       **({"valid_to": str(v)} if (v := g.value(c, SCHEMA.validThrough)) is not None else {}),
                       **({"derived_from": sorted(str(d).removeprefix("urn:kk:claim:")
                                                  for d in g.objects(c, PROV.wasDerivedFrom))}
                          if (c, PROV.wasDerivedFrom, None) in g else {})},
                "status": str(status).removeprefix("urn:kk:status:") if status is not None else None,
                "reason": str(g.value(c, SKOS.changeNote) or "") or None,
                "replaced_by": (str(r).removeprefix("urn:kk:claim:")
                                if (r := g.value(c, DCTERMS.isReplacedBy)) is not None else None)})
    claims.sort(key=lambda c: (c["recorded"], c["old"]))
    return agents, scopes, claims, artifacts


def row_key(s, p, oe, ov, q, vf, vt, agent, scope) -> str:
    """Identity of a claim's content for matching: statement, conditions, validity, agent and scope."""
    return json.dumps([s, p, oe, ov, q or {}, _iso(vf), _iso(vt), agent, scope], sort_keys=True, default=str)


def op_key(op: dict, agent: str, scope: str) -> str:
    o = op["object"]
    return row_key(op["subject"], op["predicate"], o.get("entity"), o.get("value"), op.get("qualifiers"),
                   op.get("valid_from"), op.get("valid_to"), agent, scope)


CLAIM_ROWS = """select c.id, c.subject, c.predicate, c.object_entity, c.object_value, c.qualifiers, c.valid_from,
                       c.valid_to, c.attributed_to, c.scope from kernel.claim c"""


def existing(conn, where: str = "", args: tuple = ()) -> dict[str, list[str]]:
    out = defaultdict(list)
    for cid, *rest in conn.execute(f"{CLAIM_ROWS} {where}", args).fetchall():
        out[row_key(*rest)].append(str(cid))
    return out


def import_(conn, ds: Dataset, digest: str) -> dict:
    agents, scopes, claims, artifacts = read(ds)
    owner = lambda s, *a: conn.execute(s, a)  # noqa: E731
    conn.execute("set role kk_owner")
    for iri, label in scopes.items():
        owner("insert into kernel.scope(iri, label) values (%s, %s) on conflict do nothing", iri, label)
    for iri, (label, kind) in agents.items():
        owner("insert into kernel.agent(iri, kind, label) values (%s, %s, %s) on conflict do nothing", iri, kind, label)

    def install(key: str, scope: str, ops: list, agent: str = "urn:kk:agent:kernel") -> str:
        return conn.execute("select kernel.install_ops(%s, %s, %s, %s)",
                            (f"import:{digest}:{key}", scope, Jsonb(ops), agent)).fetchone()[0]

    by_scope = defaultdict(list)
    for a in artifacts.values():
        by_scope[a.pop("scope")].append(a)
    for scope, ops in sorted(by_scope.items()):
        install(f"artifacts:{scope}", scope, ops)

    new: dict[str, str] = {}   # old claim id -> new claim id
    present = existing(conn, "where c.redacted_at is null")  # merge: a claim already here is not added again
    groups: dict[tuple, list] = {}
    for c in claims:
        groups.setdefault((c["run"], c["agent"], c["scope"]), []).append(c)
    statuses = merged = 0
    for (run, agent, scope), members in groups.items():
        ops, added = [], []
        for c in members:
            k = op_key(c["op"], agent, scope)
            if present.get(k):
                new[c["old"]] = present[k][0]
                merged += 1
                continue
            op = dict(c["op"])
            if "derived_from" in op:
                op["derived_from"] = [new.get(d, d) for d in op["derived_from"]]
            ops.append(op)
            added.append(c)
        if not ops:
            continue
        key = f"import:{digest}:claims:{run.removeprefix('urn:kk:run:')}:{scope}"
        install(key.removeprefix(f"import:{digest}:"), scope, ops, agent)
        made = existing(conn, "join kernel.change_set s on s.id = c.change_set where s.idempotency_key = %s", (key,))
        for c in added:
            new[c["old"]] = made[op_key(c["op"], agent, scope)].pop(0)
    status_ops = defaultdict(list)
    for c in claims:
        if c["status"]:
            status_ops[c["scope"]].append({"op": "set_status", "claim": new[c["old"]], "status": c["status"],
                                           **({"reason": c["reason"]} if c["reason"] else {}),
                                           **({"replaced_by": new[c["replaced_by"]]} if c["replaced_by"] in new else {})})
            statuses += 1
    for scope, ops in sorted(status_ops.items()):
        install(f"status:{scope}", scope, ops)
    conn.execute("reset role")
    return {"claims": len(claims), "agents": len(agents), "scopes": len(scopes), "artifacts": len(artifacts),
            "change_sets": len(groups), "statuses": statuses, "already_present": merged}


def _iso(v):
    if v is None:
        return None
    from datetime import datetime
    return datetime.fromisoformat(str(v)).isoformat()


# ---------------------------------------------------------------- commands
def main(cmd: str, argv: list[str]) -> None:
    import argparse

    from .cli import admin
    ap = argparse.ArgumentParser(prog=f"kk {cmd}", description=__doc__.splitlines()[0])
    if cmd == "export":
        ap.add_argument("--scope", action="append", help="only these scopes (repeatable)")
        ap.add_argument("--out", type=Path)
        a = ap.parse_args(argv)
        with admin() as conn:
            data = export(conn, a.scope).serialize(format="nquads")
        (a.out.write_text(data) if a.out else sys.stdout.write(data))
    else:
        ap.add_argument("file", type=Path)
        a = ap.parse_args(argv)
        text = a.file.read_text()
        ds = Dataset()
        ds.parse(data=text, format="nquads")
        with admin() as conn:
            print(json.dumps(import_(conn, ds, hashlib.sha256(text.encode()).hexdigest()[:16])))

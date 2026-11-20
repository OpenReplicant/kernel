"""Substrate's store -> the claims it holds, in Wikidata's and Substrate's own terms (D-103). Pure functions over
plain rows (dicts); no database, no PyYAML.

Substrate's store (schema `store`, S-135 in OpenReplicant/Substrate) is Wikibase's model: items with labels,
statements with ranks and qualifiers, references saying who stated each (a parser, an interpreter, a person, a
rule) and the span it rests on. The properties are Wikidata's (`wdt:`) and Substrate's own map and catalog terms
(`substrate:`), both packs of D-102; the properties its v1 import made at run time (`v1:field:*`) are mapped to
Backstage's relations (D-100). Nothing else is guessed: a property without a term is a finding, and its
statements are not submitted, because the kernel refuses unknown terms (KK030).

Rows in (what the CLI reads from the store, `rows.py`-free: plain dicts):
  entities    {id, kind, datatype, key, label, description, aliases, prose}
  statements  {id, subject, property, snak, value, value_entity, rank,
               qualifiers: [{ord, property, snak, value, value_entity}],
               references: [{id, by_kind, by_name, by_version, run, span: {source, locator, text} | None}]}

Out: one change set per item (`entity_change_sets`): its label claims and every statement it is the subject of.
  item                key names a pack term -> that IRI (no label claims: the pack has them); else urn:kk:substrate:<Qid>
  statement           one claim; the object an entity or a typed literal; qualifiers keyed by IRI, a repeated
                      property a list; rank preferred or deprecated -> a set_status op right after the claim
  who stated it       the claim is attributed to this encoder (the kernel allows no one else); each Substrate
                      reference becomes a kernel reference (urn:kk:substrate:reference:<uuid>?by=<kind>:<name>, its
                      span's text as the quote) and the evidence type follows the strongest of them: a parser
                      -> deterministic, else anyone else (a person, an interpreter, a rule) -> testimony
Not encoded, and counted or found: an item's prose, a quantity's unit and bounds, somevalue and novalue, statements
on properties (constraints) or to properties, coordinates, rule premises.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

AGENT = "urn:kk:agent:encoder-substrate"
SCOPE = "urn:kk:scope:default"
WDT = "http://www.wikidata.org/prop/direct/"
WD = "http://www.wikidata.org/entity/"
SUB = "https://w3id.org/knowledge-kernel/substrate#"
BS = "https://w3id.org/knowledge-kernel/backstage#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
DCT_DESCRIPTION = "http://purl.org/dc/terms/description"
SKOS_ALT = "http://www.w3.org/2004/02/skos/core#altLabel"
ITEM = "urn:kk:substrate:"

# Substrate's property keys (store/sql/001, 003, 004) -> the pack terms.
PROPERTIES = {
    "instance-of": WDT + "P31", "subclass-of": WDT + "P279", "has-part": WDT + "P527",
    "object-has-role": WDT + "P3831", "described-at-url": WDT + "P973", "point-in-time": WDT + "P585",
    "property-constraint": WDT + "P2302", "format-as-regex": WDT + "P1793", "class": WDT + "P2308",
    "item-of-property-constraint": WDT + "P2305", "reason-for-deprecated-rank": WDT + "P2241",
    "requires-interface": SUB + "requiresInterface", "provides-interface": SUB + "providesInterface",
    "for-role": SUB + "forRole", "catalog-id": SUB + "catalogId", "in-catalog": SUB + "inCatalog",
    "has-goal": SUB + "hasGoal", "metric": SUB + "metric", "target": SUB + "target",
    "measurement": SUB + "measurement", "for-goal": SUB + "forGoal", "verdict": SUB + "verdict",
    "against": SUB + "against",
    # what the v1 import made (store/import_v1.py: key f"v1:field:{name}") for v1's relations
    "v1:field:consumes_apis": BS + "consumesApi", "v1:field:provides_apis": BS + "providesApi",
    "v1:field:depends_on": BS + "dependsOn", "v1:field:subcomponent_of": BS + "partOf",
}
# Items the packs already define: their IRI, and no label claims.
ITEMS = {
    "role": SUB + "Role", "interface": SUB + "Interface", "goal": SUB + "Goal", "catalog": SUB + "Catalog",
    "verdict-class": SUB + "Verdict", "verdict-better": SUB + "better", "verdict-worse": SUB + "worse",
    "verdict-same": SUB + "same", "single-value-constraint": WD + "Q19474404",
    "format-constraint": WD + "Q21502404", "one-of-constraint": WD + "Q21510859",
    "value-type-constraint": WD + "Q21510865",
}
REASON_KEY = "reason-for-deprecated-rank"


def item_iri(entity: dict) -> str:
    return ITEMS.get(entity.get("key")) or ITEM + entity["id"]


class Findings:
    """What could not be encoded, grouped: one finding per kind and cause, with a count and a few examples."""

    def __init__(self):
        self.groups: dict[str, dict] = {}

    def add(self, kind: str, example: str, **cause):
        k = json.dumps([kind, cause], sort_keys=True)
        g = self.groups.setdefault(k, {"finding": kind, **cause, "count": 0, "examples": []})
        g["count"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(example)

    def list(self) -> list[dict]:
        return list(self.groups.values())


def decimal_text(n) -> str:
    d = Decimal(str(n)).normalize()
    return format(d, "f")


def literal(datatype: str, value) -> dict | None:
    """A store value as a JSON-LD value object, or None when it has no literal form here."""
    if datatype in ("string", "external-id") and isinstance(value, str):
        return {"@value": value}
    if datatype == "url" and isinstance(value, str):
        return {"@value": value, "@type": XSD + "anyURI"}
    if datatype == "time" and isinstance(value, dict) and isinstance(value.get("time"), str):
        return {"@value": value["time"].removeprefix("+"), "@type": XSD + "dateTime"}
    if datatype == "quantity" and isinstance(value, dict) and "amount" in value:
        try:
            return {"@value": decimal_text(value["amount"]), "@type": XSD + "decimal"}
        except (InvalidOperation, ValueError):
            return None
    return None


def reference_uri(ref: dict) -> str:
    uri = f"urn:kk:substrate:reference:{ref['id']}?by={quote(ref['by_kind'])}:{quote(ref['by_name'], safe='')}"
    return uri + (f"&version={quote(ref['by_version'], safe='')}" if ref.get("by_version") else "")


def evidence(refs: list[dict]) -> str:
    """The strongest of who stated it (spec 4, evidence ordering): a parser is deterministic; with none, someone's
    word is testimony. Substrate's interpreters and rules are testimony too, not inferred: the kernel's shapes
    (invariant 3, D-90) take `inferred` to mean derived from premise claims, and nothing here is (a rule's premises
    are statements of the store, not claims). Who it was stays in the reference (`by=<kind>:<name>`)."""
    kinds = {r["by_kind"] for r in refs}
    return "deterministic" if "parser" in kinds else "testimony"


def kernel_references(refs: list[dict]) -> list[dict]:
    out = []
    for r in refs:
        ref = {"source_uri": reference_uri(r)}
        span = r.get("span")
        if span:
            ref["quote"] = span["text"]
            ref["span"] = {"type": "FragmentSelector", "value": span["locator"], "source": span["source"]}
        out.append(ref)
    return out


class Encoder:
    def __init__(self, entities: list[dict]):
        self.by_id = {e["id"]: e for e in entities}
        self.findings = Findings()
        self.counts = {"entities": 0, "claims": 0, "labels": 0, "statements": 0, "skipped": 0, "prose": 0,
                       "properties": 0, "empty": 0, "unchanged": 0}

    # -- values ----------------------------------------------------------------------------------------
    def value(self, snak_row: dict, what: str, example: str):
        """(kind, v): ('entity', iri), ('literal', value object), or ('skip', None) with a finding."""
        prop = self.by_id[snak_row["property"]]
        if snak_row["snak"] != "value":
            self.findings.add(snak_row["snak"], example, **{what: prop.get("key") or prop["id"]})
            return "skip", None
        if snak_row.get("value_entity"):
            target = self.by_id.get(snak_row["value_entity"])
            if target is None or target["kind"] != "item":
                self.findings.add("property-as-value", example, **{what: prop.get("key") or prop["id"]})
                return "skip", None
            return "entity", item_iri(target)
        lit = literal(prop.get("datatype"), snak_row.get("value"))
        if lit is None:
            self.findings.add("unencodable-value", example, datatype=prop.get("datatype"),
                              **{what: prop.get("key") or prop["id"]})
            return "skip", None
        if prop.get("datatype") == "quantity" and (snak_row["value"].get("unit")
                                                    or "lower" in snak_row["value"] or "upper" in snak_row["value"]):
            self.findings.add("quantity-unit-not-encoded", example, **{what: prop.get("key") or prop["id"]})
        return "literal", lit

    def qualifiers(self, st: dict) -> dict:
        out: dict = {}
        for q in sorted(st.get("qualifiers") or [], key=lambda q: q["ord"]):
            prop = self.by_id[q["property"]]
            iri = PROPERTIES.get(prop.get("key"))
            if iri is None:
                self.findings.add("unmapped-qualifier", st["id"], key=prop.get("key"), property=prop["id"])
                continue
            kind, v = self.value(q, "qualifier", st["id"])
            if kind == "skip":
                continue
            plain = v if kind == "entity" else v["@value"]
            out.setdefault(iri, []).append(plain)
        return {k: v[0] if len(v) == 1 else v for k, v in out.items()}

    # -- claims ----------------------------------------------------------------------------------------
    def claim(self, st: dict) -> dict | None:
        subject, prop = self.by_id[st["subject"]], self.by_id[st["property"]]
        pred = PROPERTIES.get(prop.get("key"))
        if pred is None:
            self.findings.add("unmapped-property", st["id"], key=prop.get("key"), property=prop["id"])
            return None
        if subject["kind"] != "item":
            self.findings.add("statement-on-property", st["id"], key=prop.get("key"))
            return None
        kind, v = self.value(st, "property", st["id"])
        if kind == "skip":
            return None
        refs = st.get("references") or []
        if refs:
            ev, references = evidence(refs), kernel_references(refs)
        else:
            self.findings.add("uncited-statement", st["id"], key=prop.get("key"))
            ev, references = "testimony", [{"source_uri": f"urn:kk:substrate:statement:{st['id']}"}]
        op = {"op": "add_claim", "subject": item_iri(subject), "predicate": pred,
              "object": {"entity": v} if kind == "entity" else {"value": v},
              "evidence_type": ev, "references": references}
        quals = self.qualifiers(st)
        if quals:
            op["qualifiers"] = quals
        return op

    def labels(self, e: dict) -> list[dict]:
        if e.get("key") in ITEMS:
            return []
        s, ref = item_iri(e), [{"source_uri": f"urn:kk:substrate:entity:{e['id']}"}]
        out = []

        def add(p, text):
            out.append({"op": "add_claim", "subject": s, "predicate": p, "object": {"value": {"@value": text}},
                        "evidence_type": "deterministic", "references": ref})
        add(RDFS_LABEL, e["label"])
        if e.get("description"):
            add(DCT_DESCRIPTION, e["description"])
        for a in e.get("aliases") or []:
            add(SKOS_ALT, a)
        return out

    def run(self, statements: list[dict]) -> list[tuple[str, str, list[dict]]]:
        """[(item id, its iri, its ops)] in id order; ops are add_claims with set_status after the ranked ones."""
        mine: dict[str, list[dict]] = {}
        for e in self.by_id.values():
            if e["kind"] == "property":
                self.counts["properties"] += 1
            else:
                mine[e["id"]] = []
                self.counts["prose"] += bool((e.get("prose") or "").strip())
        for st in statements:
            if st["subject"] in mine:
                op = self.claim(st)
                if op is None:
                    self.counts["skipped"] += 1
                else:
                    rank = st.get("rank", "normal")
                    mine[st["subject"]].append((op, rank))
                    self.counts["statements"] += 1
            else:
                self.claim(st)   # a statement on a property: the finding says why
                self.counts["skipped"] += 1
        out = []
        for eid in sorted(mine, key=lambda i: int(i[1:])):
            e = self.by_id[eid]
            labels = self.labels(e)
            self.counts["labels"] += len(labels)
            ranked = sorted(mine[eid], key=lambda t: json.dumps(t, sort_keys=True))
            ops: list[dict] = []
            for op, rank in [(o, "normal") for o in labels] + ranked:
                op = dict(op, ref=f"c{len(ops)}")
                ops.append(op)
                if rank in ("preferred", "deprecated"):
                    ops.append({"op": "set_status", "claim": op["ref"], "status": rank,
                                **({"reason": "retracted"} if rank == "deprecated" else {})})
            if not ops:
                self.counts["empty"] += 1
                continue
            self.counts["entities"] += 1
            self.counts["claims"] += sum(o["op"] == "add_claim" for o in ops)
            out.append((eid, item_iri(e), ops))
        return out


def encode_store(entities: list[dict], statements: list[dict]) -> tuple[list[tuple[str, str, list[dict]]], list[dict], dict]:
    """(item id, iri, ops) per item with something to say, the findings, and the counts."""
    enc = Encoder(entities)
    result = enc.run(statements)
    return result, enc.findings.list(), enc.counts


def key_prefix(item_id: str) -> str:
    return f"substrate:{item_id}:"


def content_hash(ops: list[dict]) -> str:
    return hashlib.sha256(json.dumps(ops, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def change_set(item_id: str, ops: list[dict], scope: str = SCOPE) -> dict:
    """One item's change set, keyed by the item and the content of its ops (before any superseding)."""
    return {"idempotency_key": f"{key_prefix(item_id)}{content_hash(ops)}", "attributed_to": AGENT,
            "target_scope": scope, "ops": [dict(o) for o in ops]}


def same(s, p, entity, value, qualifiers) -> str:
    return json.dumps([s, p, entity, value, qualifiers or {}], sort_keys=True, default=str)


def supersede(cs: dict, prior: list[dict]) -> None:
    """Deprecate `prior` (what this encoder said about the item before): superseded by the same statement in `cs`
    when it states it again, else retracted. Nothing is deleted."""
    new = {}
    for op in cs["ops"]:
        if op["op"] == "add_claim":
            o = op["object"]
            new[same(op["subject"], op["predicate"], o.get("entity"), o.get("value"), op.get("qualifiers"))] = op["ref"]
    for c in prior:
        by = new.get(same(c["subject"], c["predicate"], c["object_entity"], c["object_value"], c["qualifiers"]))
        cs["ops"].append({"op": "set_status", "claim": str(c["id"]), "status": "deprecated",
                          "expect_status": c["status"],
                          **({"reason": "superseded", "replaced_by": by} if by else {"reason": "retracted"})})

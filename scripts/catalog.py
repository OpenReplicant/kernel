"""The module catalog (D-78): what each module takes in and gives out, from the manifests.

Each `accepts` / `produces` entry of a manifest is one token, optionally followed by a note in
parentheses:
  * a media type (`text/csv`, `application/bpmn+xml`): what an encoder reads or a decoder writes;
    `*/*` for any file
  * a vocabulary term as a CURIE (`bbo:has_nextFlowNode`, `sosa:Observation`): what a processor reads
    or what a module claims; it must be defined in a pack file, or be in the namespace of a pack loaded
    whole (rdf, rdfs, owl, skos, prov, ...)
  * a change-set op kind (`add_claim`, `set_status`, ...) or a kernel record (`change_set`, `claim`,
    `artifact`, `run`, `verification`, `belief`, `redaction`)

  python scripts/catalog.py           write docs/modules.md
  python scripts/catalog.py --check   fail if a token is not one of these, a trigger pattern names an unknown
                                      term, kk.lib's prefixes disagree with the packs, or docs/modules.md is stale
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/modules.md"
MEDIA = re.compile(r"^(\*/\*|[a-z]+/[a-z0-9][a-z0-9.+-]*)$")
CURIE = re.compile(r"^([a-z][a-z0-9]*):([A-Za-z_][A-Za-z0-9_.-]*)$")
RECORDS = {"change_set", "claim", "artifact", "run", "verification", "belief", "redaction"}
KINDS = ["encoder", "processor", "decoder", "actuator", "agent", "control", "interface", "pack"]
BOOT = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "xsd": "http://www.w3.org/2001/XMLSchema#"}


def vocabulary() -> tuple[dict[str, str], set[str], set[str]]:
    """(prefix -> namespace, terms defined in pack files, namespaces of packs loaded whole)."""
    import rdflib
    doc = yaml.safe_load((ROOT / "ontology/packs.yaml").read_text())
    packs = [p for tier in ("core", "profile") for p in doc.get(tier) or []]
    g, prefixes = rdflib.Graph(), dict(BOOT)
    for p in packs:
        if p.get("file"):
            one = rdflib.Graph()
            one.parse(ROOT / "ontology" / p["file"])
            g += one
            text = (ROOT / "ontology" / p["file"]).read_text()   # the file's own prefixes, not rdflib's defaults
            for k, v in re.findall(r"^@prefix\s+([a-z][a-z0-9]*):\s*<([^>]+)>", text, re.M):
                prefixes.setdefault(k, v)
    whole = {p["iri"] for p in packs if not p.get("file") and p.get("iri")} | set(BOOT.values())
    return prefixes, {str(s) for s in g.subjects()}, whole


def ops() -> set[str]:
    schema = json.loads((ROOT / "kernel/schemas/change_set.schema.json").read_text())
    return set(re.findall(r'"const":\s*"([a-z_]+)"', json.dumps(schema))) | \
        {o for e in re.findall(r'"enum":\s*\[([^\]]*)\]', json.dumps(schema)) for o in re.findall(r'"(add_\w+|set_\w+)"', e)}


def token(entry: str) -> tuple[str, str]:
    """(token, note) of one entry."""
    m = re.match(r"^(\S+)(?:\s+\((.*)\))?$", entry.strip())
    return (m.group(1), m.group(2) or "") if m else (entry, "")


def problems(entry: str, prefixes, terms, whole, op_kinds) -> str | None:
    tok, _ = token(entry)
    if MEDIA.match(tok) or tok in RECORDS or tok in op_kinds:
        return None
    m = CURIE.match(tok)
    if not m:
        return "not a media type, a CURIE, an op kind or a kernel record"
    ns = prefixes.get(m.group(1))
    if ns is None:
        return f"unknown prefix {m.group(1)}:"
    if ns + m.group(2) in terms or ns in whole:
        return None
    return f"{ns}{m.group(2)} is not defined in any pack"


def manifests() -> list[dict]:
    return [yaml.safe_load(p.read_text()) | {"folder": p.parent.name}
            for p in sorted(ROOT.glob("modules/*/module.yaml")) if p.parent.name != "_template"]


def render(ms: list[dict]) -> str:
    def cell(entries):
        out = []
        for e in entries or []:
            tok, note = token(e)
            out.append(f"`{tok}`" + (f" ({note})" if note else ""))
        return "<br>".join(out) or "—"

    def triggers(m):
        return "<br>".join(f"{t['type']}" + (f": {t['spec']}" if t.get("spec") else "") for t in m.get("triggers", [])) or "—"
    lines = ["# Modules: what each takes in and gives out", "",
             "Generated from the manifests by `python scripts/catalog.py` (D-78); CI fails when it is stale. An",
             "**encoder** turns a source format (media type) into claims; a **decoder** turns claims into a format;",
             "a **processor** reads claims (terms) and writes claims, beliefs or verifications; an **actuator**",
             "acts on the world from approved claims; an **agent** module is a skill a harness agent follows.",
             "Terms are CURIEs of the vocabulary packs (`ontology/packs.yaml`); ops are change-set operations.", ""]
    for kind in KINDS:
        these = [m for m in ms if m["kind"] == kind]
        if not these:
            continue
        lines += [f"## {kind.capitalize()}s", "", "| Module | Slots | Takes in | Gives out | Runs on |",
                  "| --- | --- | --- | --- | --- |"]
        for m in these:
            lines.append(f"| **{m['folder']}** {m['version']}<br>{m['name']} | {', '.join(m['slots'])} | "
                         f"{cell(m.get('accepts'))} | {cell(m.get('produces'))} | {triggers(m)} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv[1:]
    ms = manifests()
    prefixes, terms, whole = vocabulary()
    op_kinds = ops()
    errors = [f"modules/{m['folder']}/module.yaml {key}: {e!r}: {why}"
              for m in ms for key in ("accepts", "produces") for e in m.get(key) or []
              if (why := problems(e, prefixes, terms, whole, op_kinds))]
    for m in ms:   # trigger patterns (D-79): each term a known one, or `?`, or a prefix ending in `*`
        for t in m.get("triggers", []):
            if t["type"] != "pattern":
                continue
            parts = str(t.get("spec", "")).split()
            if len(parts) != 3:
                errors.append(f"modules/{m['folder']}/module.yaml trigger pattern {t.get('spec')!r}: not 'S P O'")
            for part in parts:
                if part != "?" and not part.endswith("*") and CURIE.match(part) and \
                        (why := problems(part, prefixes, terms, whole, op_kinds)):
                    errors.append(f"modules/{m['folder']}/module.yaml trigger pattern {part!r}: {why}")
    import ast   # kk.lib needs psycopg; read its PREFIXES from the source
    tree = ast.parse((ROOT / "kk/lib.py").read_text())
    PREFIXES = next(ast.literal_eval(n.value) for n in tree.body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PREFIXES")
    errors += [f"kk/lib.py PREFIXES[{k!r}] is {v}, the packs say {prefixes.get(k)}"
               for k, v in PREFIXES.items() if prefixes.get(k) != v]
    text = render(ms) + "\n"
    if check and (not OUT.exists() or OUT.read_text() != text):
        errors.append("docs/modules.md is stale: run python scripts/catalog.py")
    for e in errors:
        print("ERROR", e)
    if not check and not errors:
        OUT.write_text(text)
    print(f"{len(ms)} modules catalogued, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

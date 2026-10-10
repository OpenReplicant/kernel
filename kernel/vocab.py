"""Vocabularies: loading them, IRI prefixes, and the schema they declare.

A vocabulary file declares types, roles, predicates, named lists (for `enum_from`) and
constraints. Loading one records it in kb.vocabulary and creates nodes for its types
(kind `type`), roles (kind `role`) and predicates (kind `predicate`). The kernel keeps an
in-memory Schema rebuilt from kb.vocabulary on connect, which `assert_` and `validate` use.
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

KERNEL_VOCAB = Path(__file__).resolve().parent.parent / "vocab" / "kernel.yaml"
SECTIONS = {"vocabulary", "version", "imports", "prefix", "types", "roles", "predicates", "constraints"}
LITERAL = "literal"

_CURIE = re.compile(r"^([A-Za-z][\w-]*):(.+)$")


class Prefixes:
    """CURIE expansion (`k:plays` -> `urn:pc:kernel:plays`). Full IRIs pass through."""

    def __init__(self):
        self.map: dict[str, str] = {}

    def add(self, mapping: dict[str, str]):
        for p, ns in mapping.items():
            if self.map.get(p, ns) != ns:
                raise ValueError(f"prefix {p!r} already bound to {self.map[p]!r}")
            self.map[p] = ns

    def expand(self, ref: str) -> str:
        if ref.startswith("urn:") or "://" in ref:
            return ref
        m = _CURIE.match(ref)
        if not m or m.group(1) not in self.map:
            raise KeyError(f"cannot expand {ref!r}: unknown prefix (known: {sorted(self.map)})")
        return self.map[m.group(1)] + m.group(2)


@dataclass
class Schema:
    """What the loaded vocabularies declare, with every IRI expanded."""
    vocabularies: dict[str, dict] = field(default_factory=dict)     # name -> body
    types: dict[str, dict] = field(default_factory=dict)            # iri -> {kinds, parents, vocab}
    roles: dict[str, dict] = field(default_factory=dict)            # iri -> props (+ vocab)
    predicates: dict[str, dict] = field(default_factory=dict)       # iri -> props (+ vocab)
    lists: dict[str, dict] = field(default_factory=dict)            # vocab -> {list name: values}
    constraints: list[tuple[str, dict]] = field(default_factory=list)
    active: set[str] = field(default_factory=set)                   # loaded by this kernel instance

    def add(self, body: dict, prefixes: Prefixes):
        name = body["vocabulary"]
        x = prefixes.expand
        self.vocabularies[name] = body
        self.lists[name] = {k: (list(v) if isinstance(v, dict) else v)
                            for k, v in body.items() if k not in SECTIONS and isinstance(v, (list, dict))}
        for t in body.get("types", []):
            kinds = t.get("kind")
            parents = t.get("is_a", [])
            self.types[x(t["iri"])] = {
                "kinds": [kinds] if isinstance(kinds, str) else (kinds or []),
                "parents": [x(p) for p in ([parents] if isinstance(parents, str) else parents)],
                "vocab": name}
        for r in body.get("roles", []):
            self.roles[x(r["iri"])] = {**r, "iri": x(r["iri"]), "vocab": name}
        for p in body.get("predicates", []):
            props = {**p, "iri": x(p["iri"]), "vocab": name,
                     "domain": x(p["domain"]) if p.get("domain") else None,
                     "range": p["range"] if p.get("range") == LITERAL else x(p["range"])}
            self.predicates[props["iri"]] = props
        self.constraints.extend((name, c) for c in body.get("constraints", []))

    def ancestors(self, type_iri: str) -> set[str]:
        """The type and every type it is_a, transitively."""
        seen, todo = set(), [type_iri]
        while todo:
            t = todo.pop()
            if t not in seen:
                seen.add(t)
                todo.extend(self.types.get(t, {}).get("parents", []))
        return seen

    def kinds_backing(self, type_iri: str) -> set[str]:
        return set(self.types.get(type_iri, {}).get("kinds", []))

    def enum_values(self, pred: dict):
        if "enum" in pred:
            return pred["enum"]
        if "enum_from" in pred:
            return self.lists.get(pred["vocab"], {}).get(pred["enum_from"])
        return None


def read(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def record(conn, body: dict, sha: str) -> bool:
    """Store a vocabulary in kb.vocabulary. True if newly recorded. A changed file must bump
    its version."""
    row = conn.execute("select sha256 from kb.vocabulary where name = %s and version = %s",
                       (body["vocabulary"], str(body["version"]))).fetchone()
    if row and row[0] != sha:
        raise ValueError(f"vocabulary {body['vocabulary']} {body['version']} is already loaded "
                         "with different content; bump its version")
    if not row:
        conn.execute("insert into kb.vocabulary (name, version, sha256, body) values (%s, %s, %s, %s)",
                     (body["vocabulary"], str(body["version"]), sha, Jsonb(body)))
    return not row


def load_recorded(conn, prefixes: Prefixes, schema: Schema):
    """Rebuild prefixes and schema from every recorded vocabulary (latest version per name)."""
    rows = conn.execute("""select distinct on (name) body from kb.vocabulary
                           order by name, loaded_at desc""").fetchall()
    bodies = {b["vocabulary"]: b for (b,) in rows}
    for b in bodies.values():
        prefixes.add(b.get("prefix", {}))
    for name in _import_order(bodies):
        schema.add(bodies[name], prefixes)


def _import_order(bodies: dict) -> list[str]:
    order, seen = [], set()

    def visit(n):
        if n in seen or n not in bodies:
            return
        seen.add(n)
        for d in bodies[n].get("imports", []):
            visit(d)
        order.append(n)
    for n in sorted(bodies):
        visit(n)
    return order


def load(kernel, path) -> dict:
    """Load one vocabulary file into the kernel (idempotent). Its imports must be loaded."""
    body, sha = read(path)
    missing = [i for i in body.get("imports", []) if i not in kernel.schema.vocabularies]
    if missing:
        raise ValueError(f"vocabulary {body['vocabulary']} imports {missing}, which are not loaded")
    with kernel.conn.transaction():
        record(kernel.conn, body, sha)
        kernel.prefixes.add(body.get("prefix", {}))
        x = kernel.prefixes.expand
        for t in body.get("types", []):
            kernel.ensure_node("type", x(t["iri"]), props={k: v for k, v in t.items() if k != "iri"})
        for r in body.get("roles", []):
            kernel.ensure_node("role", x(r["iri"]), props={k: v for k, v in r.items() if k != "iri"})
        for p in body.get("predicates", []):
            kernel.ensure_node("predicate", x(p["iri"]), props={k: v for k, v in p.items() if k != "iri"})
    if body["vocabulary"] not in kernel.schema.vocabularies:
        kernel.schema.add(body, kernel.prefixes)
    kernel.schema.active.add(body["vocabulary"])
    return body

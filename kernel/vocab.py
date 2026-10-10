"""Vocabulary files and IRI prefixes.

M2 loads only the kernel's own vocabulary (vocab/kernel.yaml), so its predicates and types
exist as nodes. M3 generalizes this to any vocabulary file with full validation.
"""
import hashlib
import re
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

KERNEL_VOCAB = Path(__file__).resolve().parent.parent / "vocab" / "kernel.yaml"

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


def read(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def record(conn, body: dict, sha: str):
    """Store a vocabulary in kb.vocabulary; a changed file must bump its version."""
    row = conn.execute("select sha256 from kb.vocabulary where name = %s and version = %s",
                       (body["vocabulary"], str(body["version"]))).fetchone()
    if row and row[0] != sha:
        raise ValueError(f"vocabulary {body['vocabulary']} {body['version']} is already loaded "
                         "with different content; bump its version")
    if not row:
        conn.execute("insert into kb.vocabulary (name, version, sha256, body) values (%s, %s, %s, %s)",
                     (body["vocabulary"], str(body["version"]), sha, Jsonb(body)))


def load_prefixes(conn, prefixes: Prefixes):
    for (body,) in conn.execute("select body from kb.vocabulary order by loaded_at"):
        prefixes.add(body.get("prefix", {}))


def bootstrap(kernel):
    """Load vocab/kernel.yaml: its types and predicates become nodes. Idempotent."""
    body, sha = read(KERNEL_VOCAB)
    with kernel.conn.transaction():
        record(kernel.conn, body, sha)
        kernel.prefixes.add(body["prefix"])
        for t in body["types"]:
            kernel.ensure_node("type", t["iri"], props={k: v for k, v in t.items() if k != "iri"})
        for p in body["predicates"]:
            kernel.ensure_node("predicate", p["iri"], props={k: v for k, v in p.items() if k != "iri"})
    return body

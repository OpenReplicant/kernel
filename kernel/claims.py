"""Claims, fact edges, sources, agents and things (docs/CORE.md).

Every write goes through Cypher on label tables whose triggers enforce the rules: provenance
present, times and causation stamped by the database, no edits but retiring once, nothing
deleted. These functions run inside the caller's transaction; wrap them in
`kernel.db.activation(...)` or `with conn.transaction():`.

Element ids are AgensGraph graphids as text ("3.4").
"""
import hashlib
import json

from psycopg.types.json import Jsonb

METHODS = ("observed", "stated", "inferred", "computed", "defaulted")
PROVENANCE = ("method", "confidence", "source", "span", "agent", "valid_from", "valid_to")


def _one(conn, cypher, *params):
    row = conn.execute(cypher, params).fetchone()
    return row[0] if row else None


def now(conn) -> str:
    """The database's clock, in the kernel's time format."""
    return _one(conn, "SELECT kernel.now_text()")


# --- sources, agents, things ---------------------------------------------------------------

def content_hash(content) -> str:
    data = content if isinstance(content, bytes) else json.dumps(content, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def ensure_source(conn, kind: str, content, uri: str | None = None) -> str:
    """The source vertex for this content, created if new (sources are kept unchanged and
    identified by the sha256 of their content). `content` is JSON-able or bytes; bytes are
    stored by hash only."""
    sha = content_hash(content)
    found = _one(conn, "MATCH (s:source) WHERE s.sha256 = %s RETURN id(s)::text", Jsonb(sha))
    if found:
        return found
    props = {"kind": kind, "sha256": sha, "uri": uri}
    if not isinstance(content, bytes):
        props["content"] = content
    return _one(conn, "CREATE (s:source %s) RETURN id(s)::text", Jsonb(_clean(props)))


def ensure_agent(conn, name: str, kind: str = "module", version: str = "0") -> str:
    """The held agent vertex with this name and version, created if new."""
    found = _one(conn, """MATCH (a:agent) WHERE a.name = %s AND a.version = %s AND a.retired_at IS NULL
                          RETURN id(a)::text""", Jsonb(name), Jsonb(version))
    if found:
        return found
    return _one(conn, "CREATE (a:agent %s) RETURN id(a)::text",
                Jsonb({"name": name, "kind": kind, "version": version}))


def find_agent(conn, name: str, version: str | None = None) -> str | None:
    """The held agent with this name (and version, if given); the newest if several."""
    if version is not None:
        return _one(conn, """MATCH (a:agent) WHERE a.name = %s AND a.version = %s AND a.retired_at IS NULL
                             RETURN id(a)::text""", Jsonb(name), Jsonb(version))
    return _one(conn, """MATCH (a:agent) WHERE a.name = %s AND a.retired_at IS NULL
                         RETURN id(a)::text ORDER BY a.recorded_at DESC LIMIT 1""", Jsonb(name))


def ensure_thing(conn, label: str, key: str, props: dict | None = None) -> str:
    """The thing with this label and key, created if new. `props` (e.g. an embedding) are set
    on creation and updated on an existing thing; the key is its identity and never changes."""
    _check_label(label)
    found = _one(conn, f"MATCH (t:{label}) WHERE t.key = %s RETURN id(t)::text", Jsonb(key))
    if found:
        if props:
            conn.execute(f"MATCH (t:{label}) WHERE id(t) = %s::graphid SET t += %s", (found, Jsonb(props)))
        return found
    return _one(conn, f"CREATE (t:{label} %s) RETURN id(t)::text", Jsonb({**(props or {}), "key": key}))


# --- claims ----------------------------------------------------------------------------------

def assert_claim(conn, subject: str, predicate: str, value=None, *, object: str | None = None,
                 method: str, confidence: float, source: str, agent: str, span=None,
                 valid_from: str | None = None, valid_to: str | None = None) -> str:
    """Write a claim vertex about `subject` with a literal `value` or an `object` thing, linked
    to its subject, object, source (with span) and agent. Returns its id."""
    if (value is None) == (object is None):
        raise ValueError("a claim has exactly one of value or object")
    props = _clean({"predicate": predicate, "value": value, "method": method, "confidence": confidence,
                    "source": source, "agent": agent, "span": span,
                    "valid_from": valid_from, "valid_to": valid_to})
    claim = _one(conn, """MATCH (s), (src:source), (a:agent)
                          WHERE id(s) = %s::graphid AND id(src) = %s::graphid AND id(a) = %s::graphid
                          CREATE (c:claim %s), (c)-[:about]->(s), (c)-[:cites %s]->(src), (c)-[:made_by]->(a)
                          RETURN id(c)::text""",
                 subject, source, agent, Jsonb(props), Jsonb(_clean({"span": span})))
    if claim is None:
        raise LookupError(f"subject {subject}, source {source} or agent {agent} not found")
    if object is not None:
        conn.execute("""MATCH (c:claim), (o) WHERE id(c) = %s::graphid AND id(o) = %s::graphid
                        CREATE (c)-[:object]->(o)""", (claim, object))
    return claim


def supersede(conn, old: str, **new) -> str:
    """Replace claim `old` with a new claim (same subject and predicate unless given), linked
    `supersedes` → old; the old claim is retired (status superseded, retired_at stamped by the
    database). Keyword arguments are those of assert_claim."""
    row = conn.execute("""MATCH (c:claim)-[:about]->(s) WHERE id(c) = %s::graphid
                          RETURN id(s)::text, c.predicate, c.retired_at""", (old,)).fetchone()
    if row is None:
        raise LookupError(f"claim {old} not found")
    if row[2] is not None:
        raise ValueError(f"claim {old} is already retired")
    subject = new.pop("subject", row[0])
    predicate = new.pop("predicate", row[1])
    value = new.pop("value", None)
    replacement = assert_claim(conn, subject, predicate, value, **new)
    mark_superseded(conn, replacement, old)
    return replacement


def mark_superseded(conn, new: str, old: str) -> None:
    """Link `new` supersedes → `old` and retire `old` (for a claim that replaces several)."""
    conn.execute("""MATCH (n:claim), (o:claim) WHERE id(n) = %s::graphid AND id(o) = %s::graphid
                    CREATE (n)-[:supersedes]->(o) SET o.status = 'superseded'""", (new, old))


def retract(conn, claim: str) -> None:
    """Stop holding a claim without a replacement."""
    conn.execute("MATCH (c:claim) WHERE id(c) = %s::graphid SET c.status = 'retracted'", (claim,))


def held(conn, subject: str, predicate: str | None = None, *, known_at: str | None = None,
         valid_at: str | None = None) -> list[dict]:
    """Claims about `subject` (optionally one predicate) that were held at `known_at` (recorded
    by then and not yet retired) and are true at `valid_at`. Both default to now."""
    t = known_at or now(conn)
    v = valid_at or t
    by_predicate = "AND c.predicate = %s" if predicate is not None else ""
    params = (subject, *((Jsonb(predicate),) if predicate is not None else ()), Jsonb(t), Jsonb(t), Jsonb(v), Jsonb(v))
    rows = conn.execute(
        f"""MATCH (c:claim)-[:about]->(s)
           WHERE id(s) = %s::graphid {by_predicate}
             AND c.recorded_at <= %s AND (c.retired_at IS NULL OR c.retired_at > %s)
             AND (c.valid_from IS NULL OR c.valid_from <= %s) AND (c.valid_to IS NULL OR c.valid_to > %s)
           OPTIONAL MATCH (c)-[:object]->(o)
           RETURN id(c)::text, properties(c), id(o)::text ORDER BY c.recorded_at""", params).fetchall()
    return [{"id": cid, **props, **({"object": oid} if oid else {})} for cid, props, oid in rows]


def why(conn, claim: str) -> dict:
    """A claim's provenance: its properties, source (with span), agent, what it superseded and
    what superseded it."""
    row = conn.execute(
        """MATCH (c:claim)-[k:cites]->(src:source), (c)-[:made_by]->(a:agent), (c)-[:about]->(s)
           WHERE id(c) = %s::graphid
           OPTIONAL MATCH (c)-[:supersedes]->(prev:claim)
           OPTIONAL MATCH (next:claim)-[:supersedes]->(c)
           RETURN properties(c), id(s)::text, properties(src), properties(k), properties(a),
                  id(prev)::text, id(next)::text""", (claim,)).fetchone()
    if row is None:
        raise LookupError(f"claim {claim} not found")
    props, subject, src, cites, agent, prev, nxt = row
    return {"claim": {"id": claim, **props}, "subject": subject, "source": src, "span": cites.get("span"),
            "agent": agent, "supersedes": prev, "superseded_by": nxt}


# --- fact edges ------------------------------------------------------------------------------

def relate(conn, label: str, frm: str, to: str, *, method: str, confidence: float, source: str,
           agent: str, span=None, valid_from: str | None = None, valid_to: str | None = None,
           props: dict | None = None) -> str:
    """Write a fact edge (a relation the ontology declares `as: edge`) with provenance."""
    _check_label(label)
    p = _clean({**(props or {}), "method": method, "confidence": confidence, "source": source,
                "agent": agent, "span": span, "valid_from": valid_from, "valid_to": valid_to})
    edge = _one(conn, f"""MATCH (a), (b) WHERE id(a) = %s::graphid AND id(b) = %s::graphid
                           CREATE (a)-[r:{label} %s]->(b) RETURN id(r)::text""", frm, to, Jsonb(p))
    if edge is None:
        raise LookupError(f"{frm} or {to} not found")
    return edge


def retire_edge(conn, label: str, edge: str, status: str = "retracted") -> None:
    _check_label(label)
    conn.execute(f"MATCH ()-[r:{label}]->() WHERE id(r) = %s::graphid SET r.status = %s", (edge, Jsonb(status)))


# --- helpers ---------------------------------------------------------------------------------

def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _check_label(label: str) -> None:
    if not label.replace("_", "").isalnum() or not label[0].isalpha():
        raise ValueError(f"bad label name {label!r}")


__all__ = ["METHODS", "PROVENANCE", "now", "content_hash", "ensure_source", "ensure_agent", "find_agent",
           "ensure_thing", "assert_claim", "supersede", "mark_superseded", "retract", "held", "why", "relate", "retire_edge",
           ]

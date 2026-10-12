"""Shared chain node: write things, claims and fact edges with provenance (x/python).

Message in:
    {"source": {"id": "5.1"} | {"kind": "command", "content": {...}, "uri": "..."},   # optional
     "things": [{"ref": "n", "label": "note", "key": "alpha", "props": {"embedding": [..]}}],
     "claims": [{"subject": "n" | {"id": ".."} | {"label": "note", "key": "alpha"},
                 "predicate": "notes.status", "value": "draft" | "object": <ref>,
                 "method": "stated", "confidence": 0.9, "span": "$.status",
                 "valid_from": "...", "valid_to": "...", "derived_from": ["<claim id>", ...],
                 "replaces": "current" | "<claim id>",                           # optional
                 "if_current": ["<claim id>", ...]}],                            # optional
     "edges":  [{"label": "links_to", "from": <ref>, "to": <ref>, "method": ..., ...}]}
`if_current` is a compare-and-set: the claim is written only if the held claims with its subject
and predicate are exactly these ids (`[]`: none held); otherwise it is skipped and listed in
`written.skipped`. Writes that replace or compare are serialized per subject and predicate.

Message out: {"written": {"source": id, "things": {ref: id}, "claims": [ids], "edges": [ids]}}, plus
any other fields of the message in, passed through. A message with nothing to write writes
nothing (not even a source).

The agent is the chain's module (chain var `module`, set by the module loader). The write runs
in one transaction marked with the activation's causation and depth (metadata from the
bridge), so the loop guard sees it. Without a `source`, a write caused by a change cites that
change (`outbox:<seq>`); a write with neither is refused.
"""
import os

WRITE_FIELDS = ("source", "things", "claims", "edges")

from psycopg.types.json import Jsonb

from kernel import claims, db

CLAIM_FIELDS = ("method", "confidence", "span", "valid_from", "valid_to")


def write(conn, msg: dict, module: str, version: str | None, causation: str | None, depth: int) -> dict:
    agent = claims.find_agent(conn, f"module:{module}", version)
    if agent is None:
        raise LookupError(f"no agent for module {module!r} {version or ''}: is it installed?")
    out = {"things": {}, "claims": [], "edges": []}
    with db.activation(conn, causation, depth):
        out["source"] = _source(conn, msg.get("source"), causation)
        for t in msg.get("things", []):
            out["things"][t.get("ref", t["key"])] = claims.ensure_thing(conn, t["label"], t["key"], t.get("props"))

        def ref(r):
            if isinstance(r, str) and r in out["things"]:
                return out["things"][r]
            if isinstance(r, dict) and "id" in r:
                return r["id"]
            if isinstance(r, dict) and "label" in r:
                claims._check_label(r["label"])
                found = conn.execute(f"MATCH (t:{r['label']}) WHERE t.key = %s RETURN id(t)::text",
                                     (Jsonb(r["key"]),)).fetchone()
                if found:
                    return found[0]
            raise LookupError(f"unknown reference {r!r}")

        for c in msg.get("claims", []):
            subject = ref(c["subject"])
            kw = {k: c[k] for k in CLAIM_FIELDS if k in c}
            kw.update(source=out["source"], agent=agent)
            if "object" in c:
                kw["object"] = ref(c["object"])
            if c.get("derived_from"):
                kw["derived_from"] = list(c["derived_from"])  # claim ids
            if c.get("replaces") is not None or "if_current" in c:
                # one writer at a time per subject and predicate, until this transaction ends
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{subject}|{c['predicate']}",))
            if "if_current" in c:
                held = sorted(h["id"] for h in claims.held(conn, subject, c["predicate"]))
                if held != sorted(c["if_current"] or []):
                    out.setdefault("skipped", []).append({"predicate": c["predicate"], "subject": subject,
                                                          "expected": c["if_current"], "held": held})
                    continue
            old = _replaced(conn, c.get("replaces"), subject, c["predicate"])
            new = claims.assert_claim(conn, subject, c["predicate"], c.get("value"), **kw)
            for o in old:
                claims.mark_superseded(conn, new, o)
            out["claims"].append(new)
        for e in msg.get("edges", []):
            kw = {k: e[k] for k in CLAIM_FIELDS if k in e}
            out["edges"].append(claims.relate(conn, e["label"], ref(e["from"]), ref(e["to"]),
                                              source=out["source"], agent=agent, props=e.get("props"), **kw))
    return out


def _source(conn, spec, causation):
    if spec and "id" in spec:
        return spec["id"]
    if spec:
        return claims.ensure_source(conn, spec["kind"], spec["content"], spec.get("uri"))
    if causation:
        return claims.ensure_source(conn, "change", {"causation": causation}, causation)
    raise ValueError("a write needs a source (or must be caused by a change)")


def _replaced(conn, replaces, subject, predicate):
    if replaces is None:
        return []
    if replaces == "current":
        return [h["id"] for h in claims.held(conn, subject, predicate)]
    return [replaces]


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    module = (vars or {}).get("module")
    if not module:
        raise ValueError("graph_write needs the chain var 'module' (deploy chains with kernel.modules)")
    rest = {k: v for k, v in msg.items() if k not in WRITE_FIELDS}
    if not any(msg.get(k) for k in ("things", "claims", "edges")):
        return {"msg": {**rest, "written": None}, "metadata": metadata, "msgType": msgType}
    causation, depth = db.activation_from_metadata(metadata)
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        written = write(conn, msg, module, (vars or {}).get("version"), causation, depth)
    return {"msg": {**rest, "written": written}, "metadata": metadata, "msgType": msgType}

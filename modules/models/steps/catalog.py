"""models_catalog: a model catalog -> stated claims about endpoints and models (for graph_write).

In: {} (the module's catalog.yaml, chain var `catalog`) or {"catalog": {...}, "uri": "..."}.
The catalog is the source; each claim's span points into it ($.models[1].prices). A field
whose held claim already says the same writes nothing; a changed one replaces the held claim.
"""
import os

import yaml
from psycopg.types.json import Jsonb

from kernel import claims, db

ENDPOINT_FIELDS = ("api", "base_url", "key_env")


def facts(catalog: dict):
    """(label, key, predicate, value | ('ref', label, key), span) for every field."""
    for i, e in enumerate(catalog.get("endpoints") or []):
        for f in ENDPOINT_FIELDS:
            if f in e:
                yield "endpoint", e["id"], f"models.{f}", e[f], f"$.endpoints[{i}].{f}"
    for i, m in enumerate(catalog.get("models") or []):
        yield ("model", m["id"], "models.model_id", m.get("model_id", m["id"]),
               f"$.models[{i}].{'model_id' if 'model_id' in m else 'id'}")
        yield "model", m["id"], "models.served_by", ("ref", "endpoint", m["endpoint"]), f"$.models[{i}].endpoint"
        for f in ("prices", "params"):
            if f in m:
                yield "model", m["id"], f"models.{f}", m[f], f"$.models[{i}].{f}"


def held_value(conn, label, key, predicate):
    row = conn.execute(f"MATCH (t:{label}) WHERE t.key = %s RETURN id(t)::text", (Jsonb(key),)).fetchone()
    if row is None:
        return None, False
    held = claims.held(conn, row[0], predicate)
    if not held:
        return None, False
    h = held[-1]
    if "object" in h:
        ref = conn.execute("MATCH (t) WHERE id(t) = %s::graphid RETURN label(t), t.key", (h["object"],)).fetchone()
        return ("ref", ref[0], ref[1]), True
    return h["value"], True


def Process(msg, metadata, msgType, dataType, vars=None, globals=None):
    if msg.get("catalog"):
        catalog, uri = msg["catalog"], msg.get("uri") or "catalog:inline"
    else:
        path = (vars or {})["catalog"]
        with open(path) as f:
            catalog, uri = yaml.safe_load(f), f"file:{os.path.basename(path)}"
    things, out = {}, []
    with db.connect(os.environ.get("DATABASE_URL")) as conn:
        for label, key, predicate, value, span in facts(catalog):
            things[f"{label}:{key}"] = {"ref": f"{label}:{key}", "label": label, "key": key}
            current, exists = held_value(conn, label, key, predicate)
            if exists and current == value:
                continue
            if isinstance(value, tuple):
                things[f"{value[1]}:{value[2]}"] = {"ref": f"{value[1]}:{value[2]}", "label": value[1], "key": value[2]}
                body = {"object": f"{value[1]}:{value[2]}"}
            else:
                body = {"value": value}
            out.append({"subject": f"{label}:{key}", "predicate": predicate, **body, "method": "stated",
                        "confidence": 1.0, "span": span, "replaces": "current"})
    write = {"source": {"kind": "document", "content": catalog, "uri": uri}, "things": list(things.values()),
             "claims": out} if out else {}
    return {"msg": {**write, "changed": len(out)}, "metadata": metadata, "msgType": msgType}

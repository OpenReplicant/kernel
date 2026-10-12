"""Module manifest and loader (docs/CORE.md, "Modules").

install(path) reads `<path>/module.yaml`, installs its dependencies, creates its ontology's
labels with the kernel's triggers and indexes, records the module and its agent in the graph,
and deploys its chains to RuleGo.
"""
import hashlib
import json
import os
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from kernel import claims, db
from kernel.rulego import RuleGo

OPS = ("INSERT", "UPDATE", "DELETE")
TYPES = ("perceiver", "reasoner", "recaller", "effector", "ontology")
CORE_LABELS = {"thing", "claim", "source", "agent", "module"}


class ManifestError(ValueError):
    pass


def read_manifest(path: Path) -> dict:
    path = Path(path)
    m = yaml.safe_load((path / "module.yaml").read_text())
    for key in ("module", "version"):
        if not m.get(key):
            raise ManifestError(f"{path}/module.yaml: missing {key}")
    m["version"] = str(m["version"])
    m.setdefault("depends", [])
    m.setdefault("ontology", {})
    m["ontology"].setdefault("things", [])
    m["ontology"].setdefault("relations", [])
    m.setdefault("chains", [])
    m.setdefault("activations", [])
    m.setdefault("types", [])
    m.setdefault("vars", {})
    if not set(m["types"]) <= set(TYPES):
        raise ManifestError(f"{path}/module.yaml: types must be among {TYPES}")
    for t in m["ontology"]["things"]:
        _name(t.get("label"), "thing label")
    for r in m["ontology"]["relations"]:
        if r.get("as") not in ("claim", "edge"):
            raise ManifestError(f"relation {r.get('name')}: 'as' must be claim or edge")
        if r["as"] == "edge":
            _name(r.get("name"), "edge relation name")
    return m


def manifest_hash(path: Path, m: dict) -> str:
    h = hashlib.sha256((Path(path) / "module.yaml").read_bytes())
    for c in sorted(m["chains"]):
        h.update((Path(path) / c).read_bytes())
    return h.hexdigest()


def find_module(name: str, near: Path) -> Path:
    paths = [Path(near).parent] + [Path(p) for p in os.environ.get("KERNEL_MODULES", "").split(os.pathsep) if p]
    for p in paths:
        if (p / name / "module.yaml").exists():
            return p / name
    raise ManifestError(f"module {name} not found in {[str(p) for p in paths]}")


def install(path, conn=None, rulego: RuleGo | None = None, _seen=None) -> dict:
    """Install the module at `path` (and its dependencies). Idempotent for an unchanged
    manifest; a changed manifest under the same version is an error; a new version retires the
    old module record. Returns {module, version, id, chains, labels}."""
    path = Path(path).resolve()
    conn = conn or db.connect()
    rulego = rulego or RuleGo()
    _seen = _seen if _seen is not None else set()
    m = read_manifest(path)
    if m["module"] in _seen:
        return {"module": m["module"], "version": m["version"]}
    _seen.add(m["module"])
    for dep in m["depends"]:
        install(find_module(dep, path), conn, rulego, _seen)

    digest = manifest_hash(path, m)
    with conn.transaction():
        db.allow_ddl(conn)
        existing = conn.execute(
            """MATCH (m:module) WHERE m.name = %s AND m.retired_at IS NULL
               RETURN id(m)::text, m.version, m.hash""", (Jsonb(m["module"]),)).fetchall()
        record = None
        for mid, version, h in existing:
            if version == m["version"]:
                if h != digest:
                    raise ManifestError(f"module {m['module']} {m['version']} is installed with different "
                                        f"content; bump the version")
                record = mid
        labels = _install_ontology(conn, m)
        known = CORE_LABELS | set(labels) | _labels_of_installed(conn)
        chain_ids = [_chain_id(path / c) for c in m["chains"]]
        _check_activations(m, chain_ids, known)
        if record is None:
            for mid, version, _ in existing:  # another version is replaced, with its agent
                conn.execute("MATCH (m:module) WHERE id(m) = %s::graphid SET m.status = 'superseded'", (mid,))
                old_agent = claims.find_agent(conn, f"module:{m['module']}", version)
                if old_agent:
                    conn.execute("MATCH (a:agent) WHERE id(a) = %s::graphid SET a.status = 'superseded'", (old_agent,))
            claims.ensure_agent(conn, f"module:{m['module']}", kind="module", version=m["version"])
            record = conn.execute("CREATE (m:module %s) RETURN id(m)::text", (Jsonb({
                "name": m["module"], "version": m["version"], "hash": digest, "path": str(path), "types": m["types"],
                "labels": labels, "relations": m["ontology"]["relations"], "chains": chain_ids,
                "activations": m["activations"]}),)).fetchone()[0]
    for c in m["chains"]:
        rulego.deploy(render_chain(path / c, m, path))
    return {"module": m["module"], "version": m["version"], "id": record, "chains": chain_ids, "labels": labels}


def render_chain(file: Path, m: dict, module_dir: Path) -> dict:
    """The chain JSON as deployed: ${module_dir} and ${kernel_dir} substituted, the manifest's
    `vars` added to the chain vars, and the vars `module` and `version` set (the shared write
    node takes its agent from them)."""
    text = file.read_text().replace("${module_dir}", str(module_dir))
    text = text.replace("${kernel_dir}", str(db.ROOT))
    chain = json.loads(text)
    rc = chain["ruleChain"]
    if not rc["id"].startswith(m["module"] + "_"):
        raise ManifestError(f"chain id {rc['id']} must start with '{m['module']}_'")
    chain_vars = rc.setdefault("configuration", {}).setdefault("vars", {})
    chain_vars.update({k: str(v) for k, v in m["vars"].items()})
    chain_vars.update(module=m["module"], version=m["version"])
    return chain


def installed(conn) -> list[dict]:
    """Held module records."""
    rows = conn.execute("MATCH (m:module) WHERE m.retired_at IS NULL RETURN id(m)::text, properties(m)").fetchall()
    return [{"id": i, **p} for i, p in rows]


def _install_ontology(conn, m) -> list[str]:
    labels = []
    for t in m["ontology"]["things"]:
        label = t["label"]
        cols = ", ".join(f"{_name(k, 'vector property')} vector({int(d)}) GENERATED"
                         for k, d in (t.get("vectors") or {}).items())
        conn.execute(f"CREATE VLABEL IF NOT EXISTS {label} {'(' + cols + ')' if cols else ''} INHERITS (thing)")
        conn.execute(f"CREATE PROPERTY INDEX IF NOT EXISTS {label}_key ON {label} (key)")
        for k in (t.get("vectors") or {}):
            conn.execute(f"CREATE PROPERTY INDEX IF NOT EXISTS {label}_{k} ON {label} USING hnsw ({k} vector_l2_ops)")
        conn.execute("SELECT kernel.manage(%s::regclass, 'thing')", (f"{db.graph_name()}.{label}",))
        labels.append(label)
    for r in m["ontology"]["relations"]:
        if r["as"] == "edge":
            conn.execute(f"CREATE ELABEL IF NOT EXISTS {r['name']}")
            conn.execute("SELECT kernel.manage(%s::regclass, 'fact')", (f"{db.graph_name()}.{r['name']}",))
            labels.append(r["name"])
    return labels


def _labels_of_installed(conn) -> set[str]:
    return {l for mod in installed(conn) for l in mod.get("labels", [])}


def _check_activations(m, chain_ids, known_labels):
    for a in m["activations"]:
        if a.get("when") != "change":  # ('when', not 'on': YAML 1.1 reads a bare `on` as true)
            raise ManifestError(f"activation {a}: only 'when: change' is handled by the bridge "
                                f"(schedules live in the chain's own endpoint)")
        if a.get("chain") not in chain_ids:
            raise ManifestError(f"activation {a}: chain {a.get('chain')} is not one of this module's chains")
        if a.get("label") not in known_labels:
            raise ManifestError(f"activation {a}: unknown label {a.get('label')}")
        if not set(a.get("ops", OPS)) <= set(OPS):
            raise ManifestError(f"activation {a}: ops must be among {OPS}")


def _chain_id(file: Path) -> str:
    return json.loads(file.read_text())["ruleChain"]["id"]


def _name(n, what):
    if not n or not str(n).replace("_", "").isalnum() or not str(n)[0].isalpha() or str(n).lower() != str(n):
        raise ManifestError(f"bad {what}: {n!r} (lowercase letters, digits, _)")
    return n

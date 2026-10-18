"""kk module install DIR: put a module from this repository into a running kernel, sandboxed (T18, D-43).

1. Check the manifest (kernel/schemas/module.schema.json) and its license (D-11: permissive or LGPL).
2. Apply its migration (its role and agent row; views it adds to `api`) unless this kernel has its agent.
3. Give its role its own login: the module acts only as itself and cannot switch roles (D-37).
4. For a worker (runtime python-service or container) or a command, write a compose service to
   OUT/<name>.yaml and its login to OUT/<name>.env (never committed). The service runs locked down:
   read-only filesystem, no capabilities, no new privileges, nobody, and only the internal network that
   reaches Postgres (no internet). The sandbox policy (deploy/policy.yaml, D-55) grants the rest: memory
   and process limits, an internal network `kk-reach-<name>` for each service the module may reach, which
   only that service also joins (D-52), and a route out if granted. A module that requests network the
   policy does not grant is refused. A command's service is in the `run`
   profile: it reads its input from KK_INPUT (mounted read-only at /input) and keeps artifacts in the
   kk-artifacts volume. A worker that requires the artifact-store slot (the quote verifier) reads that
   volume read-only.

Then: docker compose -f deploy/compose.yaml -f deploy/modules/<name>.yaml up -d (a worker), or
KK_INPUT=DIR docker compose ... run --rm module-<name> ARGS... (a command, e.g. /input/log.csv).
Record its manifest in the self-model with `kk scan`. Admin connection: KK_ADMIN_URL.
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import sys
from pathlib import Path

import yaml

PERMISSIVE = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "PSF-2.0", "0BSD", "Unlicense",
              "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later"}
WORKERS = {"python-service", "container"}
SANDBOXED = WORKERS | {"command"}
GRANTS = {"reach", "egress", "memory", "pids"}


def check(manifest: dict, root: Path, allow_license: bool) -> list[str]:
    import jsonschema
    schema = json.loads((root / "kernel/schemas/module.schema.json").read_text())
    errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(manifest)]
    if manifest.get("license") not in PERMISSIVE and not allow_license:
        errors.append(f"license {manifest.get('license')!r} is not in the default distribution's allow list (D-11);"
                      " pass --allow-license to install it anyway")
    if manifest.get("runtime", {}).get("type") in SANDBOXED and not manifest.get("agent", {}).get("db_role"):
        errors.append("a worker or command needs agent.db_role (its own database role)")
    return errors


def grant(policy: dict, name: str, manifest: dict) -> tuple[dict, list[str]]:
    """D-55: the policy's grant for one module, and the manifest's requests it leaves out."""
    own = (policy.get("modules") or {}).get(name) or {}
    unknown = sorted(set(policy.get("defaults") or {}) - GRANTS) + sorted(set(own) - GRANTS)
    g = {"reach": [], "egress": False, "memory": "256m", "pids": 64, **(policy.get("defaults") or {}), **own}
    missing = [f"reach {n}" for n in manifest.get("network", {}).get("reach", []) if n not in g["reach"]]
    return g, [f"unknown policy key {k!r}" for k in unknown] + missing


def service(name: str, manifest: dict, env_file: str, image: str, g: dict | None = None) -> dict:
    g = g or grant({}, name, {})[0]
    rt = manifest["runtime"]
    svc = {
        "image": rt.get("image") if rt["type"] == "container" else image,
        "env_file": [env_file],
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "user": "nobody",
        "tmpfs": ["/tmp"],
        "mem_limit": str(g["memory"]),
        "pids_limit": int(g["pids"]),
        "networks": ["kernel-db"],
        "depends_on": {"db": {"condition": "service_healthy"}},
        "restart": "unless-stopped",
    }
    if rt["type"] == "python-service":
        svc["working_dir"] = f"/kk/modules/{name}"
        svc["command"] = shlex.split(rt["entrypoint"])
    if rt["type"] in WORKERS and any(r.get("slot") == "artifact-store" for r in manifest.get("requires", [])):
        svc.update(environment={"KK_ARTIFACTS": "/artifacts"}, volumes=["kk-artifacts:/artifacts:ro"])
        return {"services": {f"module-{name}": svc}, "volumes": {"kk-artifacts": {}}, **networks(g, svc)}
    if rt["type"] == "command":           # one-shot: docker compose run --rm module-<name> ARGS...
        words = shlex.split(rt["entrypoint"])
        svc.update(working_dir=f"/kk/modules/{name}", profiles=["run"], restart="no",
                   entrypoint=words[:next((i for i, w in enumerate(words) if w[:2] == "--" or w[:1] == "<"), len(words))],
                   environment={"KK_ARTIFACTS": "/artifacts"},
                   volumes=["${KK_INPUT:?set KK_INPUT to the input folder}:/input:ro", "kk-artifacts:/artifacts"])
        return {"services": {f"module-{name}": svc}, "volumes": {"kk-artifacts": {}}, **networks(g, svc)}
    return {"services": {f"module-{name}": svc}, **networks(g, svc)}


def networks(g: dict, svc: dict) -> dict:
    """D-52, D-55: one internal network per granted service, which only that service also joins; and
    kk-egress, the one network with a route out, if granted."""
    nets = {f"kk-reach-{n}": {"internal": True} for n in g["reach"]}
    if g["egress"]:
        nets["kk-egress"] = {}
    svc["networks"] += list(nets)
    return {"networks": nets} if nets else {}


def install(conn, folder: Path, root: Path, out: Path, db_host: str, image: str, allow_license: bool,
            policy: Path | None = None) -> dict:
    folder = folder.resolve()
    manifest = yaml.safe_load((folder / "module.yaml").read_text())
    name = folder.name
    policy = policy or root / "deploy/policy.yaml"
    g, denied = grant(yaml.safe_load(policy.read_text()) if policy.exists() else {}, name, manifest)
    errors = check(manifest, root, allow_license)
    if denied and manifest.get("runtime", {}).get("type") in SANDBOXED:
        errors.append(f"not granted by the sandbox policy {policy} (D-55): {', '.join(denied)};"
                      f" an operator grants it under modules.{name} there")
    if errors:
        sys.exit("refused: " + "; ".join(errors))
    role = manifest["agent"].get("db_role")
    result = {"module": manifest["id"], "version": str(manifest["version"]), "role": role}

    # Installed when this kernel has its agent (roles are shared by every database in the cluster).
    exists = conn.execute("select 1 from kernel.agent where iri = %s", (manifest["agent"].get("iri"),)).fetchone()
    migration = folder / manifest.get("migration", "migration.sql")
    if not exists and migration.exists():
        conn.execute("set client_min_messages = warning")
        conn.execute(migration.read_text())
        conn.execute("reset role")
    result["migration"] = "applied" if not exists and migration.exists() else "skipped (installed)" if exists else "none"

    if manifest["runtime"]["type"] in SANDBOXED:
        from psycopg import sql
        pw = secrets.token_urlsafe(24)
        conn.execute(sql.SQL("alter role {} login password {}").format(sql.Identifier(role), sql.Literal(pw)))
        out.mkdir(parents=True, exist_ok=True)
        env = out / f"{name}.env"
        env.write_text(f"DATABASE_URL=postgresql://{role}:{pw}@{db_host}/{conn.info.dbname}\nKK_ROLE={role}\n")
        env.chmod(0o600)
        compose = out / f"{name}.yaml"
        header = (f"# Generated by kk module install from modules/{name} ({manifest['id']} {manifest['version']}).\n"
                  f"# docker compose -f deploy/compose.yaml -f {compose} up -d\n")
        compose.write_text(header + yaml.safe_dump(service(name, manifest, str(env.resolve()), image, g),
                                                   sort_keys=False))
        result.update(compose=str(compose), env=str(env), service=f"module-{name}", granted=g)
    return result


def main(argv: list[str]) -> None:
    import argparse

    from .cli import ROOT, admin
    ap = argparse.ArgumentParser(prog="kk module", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("install", help="install a module folder from this repository")
    p.add_argument("folder", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "deploy/modules",
                   help="where the compose service and its login go (default deploy/modules)")
    p.add_argument("--db-host", default=os.environ.get("KK_MODULE_DB_HOST", "db:5432"),
                   help="Postgres host:port as the module's container sees it (default db:5432)")
    p.add_argument("--image", default=os.environ.get("KK_IMAGE", "knowledge-kernel:local"))
    p.add_argument("--allow-license", action="store_true")
    p.add_argument("--policy", type=Path, default=os.environ.get("KK_POLICY"),
                   help="the sandbox policy (default deploy/policy.yaml, D-55)")
    a = ap.parse_args(argv)
    with admin() as conn:
        print(json.dumps(install(conn, a.folder, ROOT, a.out, a.db_host, a.image, a.allow_license, a.policy)))

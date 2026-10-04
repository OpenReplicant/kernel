"""Packs: read a pack folder, check it against the kernel, and install its ontology.

A pack is a folder whose root is an Agent Skill (ADR 0015):

    SKILL.md      frontmatter: name, description, metadata.version, metadata.kernel (range)
    schema.yaml   namespaces, kinds and edge kinds, each with a label and a description
    rules.yaml    ontology rules (categories and params as in kernel.rules)

`load` validates the files into a manifest; `install` checks the pack's kernel range
against the database's `kernel.version()` and applies the manifest with
`kernel.install_pack`, which refuses conflicts and is idempotent. Installing is a deploy
step run with the admin DSN, never by the gateway.

    python -m kernel.packs install --database wmk [pack ...]   # default: $WMK_PACKS, else all
    python -m kernel.packs check packs/research                  # validate without installing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PACK_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_RULE_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_CATEGORIES = {"types", "domain_range", "cardinality", "time", "identity", "provenance"}
_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    # section: (required keys, optional keys)
    "namespaces": ({"name", "label", "description"}, set()),
    "kinds": ({"name", "node_type", "label", "description"}, set()),
    "edge_kinds": ({"name", "edge", "label", "description"}, set()),
    "rules": ({"id", "category", "params", "label", "description"}, {"namespace"}),
}


class PackError(Exception):
    """A pack that cannot be read, is incompatible with the kernel, or was refused."""


@dataclass(frozen=True)
class Pack:
    name: str
    version: str
    kernel_range: str
    path: Path
    manifest: dict[str, Any]

    def supports(self, kernel_version: str) -> bool:
        return Version(kernel_version) in specifier(self.kernel_range)


def specifier(kernel_range: str) -> SpecifierSet:
    """A range written as in SKILL.md (">=0.1 <1.0") or with commas, as a SpecifierSet."""
    return SpecifierSet(",".join(kernel_range.replace(",", " ").split()))


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise PackError(f"{path}: no frontmatter")
    meta = yaml.safe_load(text.split("---\n")[1]) or {}
    if not isinstance(meta, dict):
        raise PackError(f"{path}: frontmatter is not a mapping")
    return meta


def _yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise PackError(f"{path}: expected a mapping at the top level")
    return data


def _entries(where: Path, section: str, value: Any) -> list[dict[str, Any]]:
    required, optional = _FIELDS[section]
    if value is None:
        return []
    if not isinstance(value, list):
        raise PackError(f"{where}: {section} must be a list")
    for i, entry in enumerate(value):
        label = f"{where}: {section}[{i}]"
        if not isinstance(entry, dict):
            raise PackError(f"{label} must be a mapping")
        missing, unknown = required - entry.keys(), entry.keys() - required - optional
        if missing:
            raise PackError(f"{label} is missing {', '.join(sorted(missing))}")
        if unknown:
            raise PackError(f"{label} has unknown keys {', '.join(sorted(unknown))}")
        for key in ("label", "description"):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise PackError(f"{label}.{key} must be non-empty text: schema slicing reads it")
        if section == "rules":
            if not _RULE_ID.match(str(entry["id"])):
                raise PackError(f"{label}.id must look like <namespace>.<name>")
            if entry["category"] not in _CATEGORIES:
                raise PackError(f"{label}.category must be one of {', '.join(sorted(_CATEGORIES))}")
            if not isinstance(entry["params"], dict):
                raise PackError(f"{label}.params must be a mapping")
        elif not _NAME.match(str(entry["name"])):
            raise PackError(f"{label}.name must be lower case letters, digits and underscores")
    return value


def load(path: Path | str) -> Pack:
    """Read and validate a pack folder into its manifest."""
    path = Path(path)
    if not (path / "SKILL.md").exists():
        raise PackError(f"{path}: a pack's root is an Agent Skill; SKILL.md is missing")
    if (path / "sql").exists():
        raise PackError(
            f"{path}: pack SQL is not supported yet; declare the ontology in schema.yaml and rules.yaml"
        )
    meta = _frontmatter(path / "SKILL.md")
    name = meta.get("name")
    metadata = meta.get("metadata") or {}
    version, kernel_range = str(metadata.get("version", "")), str(metadata.get("kernel", ""))
    if not isinstance(name, str) or not _PACK_NAME.match(name):
        raise PackError(f"{path}/SKILL.md: name must be lower case letters, digits and hyphens")
    try:
        Version(version)
    except InvalidVersion:
        raise PackError(f"{path}/SKILL.md: metadata.version must be a version such as 0.1.0") from None
    if not kernel_range:
        raise PackError(f"{path}/SKILL.md: metadata.kernel (the kernel range the pack supports) is missing")
    try:
        specifier(kernel_range)
    except InvalidSpecifier:
        raise PackError(f"{path}/SKILL.md: metadata.kernel must be a range such as '>=0.2 <1.0'") from None

    schema, rules = _yaml(path / "schema.yaml"), _yaml(path / "rules.yaml")
    for where, data, allowed in (
        (path / "schema.yaml", schema, {"namespaces", "kinds", "edge_kinds"}),
        (path / "rules.yaml", rules, {"rules"}),
    ):
        if unknown := set(data) - allowed:
            raise PackError(f"{where}: unknown sections {', '.join(sorted(unknown))}")
    manifest = {
        "name": name,
        "version": version,
        "kernel": kernel_range,
        "namespaces": _entries(path / "schema.yaml", "namespaces", schema.get("namespaces")),
        "kinds": _entries(path / "schema.yaml", "kinds", schema.get("kinds")),
        "edge_kinds": _entries(path / "schema.yaml", "edge_kinds", schema.get("edge_kinds")),
        "rules": _entries(path / "rules.yaml", "rules", rules.get("rules")),
    }
    return Pack(name, version, kernel_range, path, manifest)


def discover(root: Path = PACKS) -> dict[str, Path]:
    """Pack folders under `root`, by pack name."""
    found = {}
    for folder in sorted(p for p in root.iterdir() if (p / "SKILL.md").exists()):
        found[load(folder).name] = folder
    return found


def resolve(names: list[str] | None = None, root: Path = PACKS) -> list[Pack]:
    """Packs by name (from `root`) or by folder path; None means $WMK_PACKS, or every pack when
    it is unset or empty."""
    if names is None:
        env = os.environ.get("WMK_PACKS", "").replace(",", " ").split()
        names = env or list(discover(root))
    known = discover(root) if any(not Path(n).exists() for n in names) else {}
    packs = []
    for name in names:
        if Path(name).is_dir():
            packs.append(load(name))
        elif name in known:
            packs.append(load(known[name]))
        else:
            raise PackError(f"no pack named {name} under {root} (known: {', '.join(known) or 'none'})")
    return packs


def install(conn: psycopg.Connection[Any], pack: Pack) -> dict[str, Any]:
    """Install one pack into the database `conn` points at (an admin connection)."""
    row = conn.execute("SELECT kernel.version()").fetchone()
    kernel_version = str(row[0]) if row else "0"
    if not pack.supports(kernel_version):
        raise PackError(
            f"{pack.name} {pack.version} supports kernel {pack.kernel_range}, not {kernel_version}"
        )
    return install_manifest(conn, pack.manifest)


def install_manifest(conn: psycopg.Connection[Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Apply a manifest as it is (replay reinstalls the manifests a database recorded)."""
    try:
        row = conn.execute("SELECT kernel.install_pack(%s)", [Jsonb(manifest)]).fetchone()
    except psycopg.Error as exc:
        if exc.sqlstate == "WMK03":
            raise PackError(exc.diag.message_primary or str(exc)) from None
        raise
    conn.commit()
    assert row is not None
    return dict(row[0])


def main() -> None:
    from kernel import admin

    parser = argparse.ArgumentParser(description="Install or check World Model Kernel packs.")
    sub = parser.add_subparsers(dest="command", required=True)
    inst = sub.add_parser("install", help="install packs into a database (admin DSN from WMK_ADMIN_DSN)")
    inst.add_argument("packs", nargs="*", help="pack names or folders (default: $WMK_PACKS, else all)")
    inst.add_argument("--database", default=os.environ.get("WMK_DATABASE", "wmk"))
    check = sub.add_parser("check", help="validate pack folders without installing them")
    check.add_argument("packs", nargs="*", help="pack names or folders (default: all)")
    args = parser.parse_args()
    try:
        packs = resolve(args.packs or None)
        if args.command == "check":
            for pack in packs:
                print(f"{pack.name} {pack.version}: ok (kernel {pack.kernel_range})")
            return
        if not packs:
            print(f"no packs to install into {args.database}")
            return
        with psycopg.connect(admin.dsn_for(admin.admin_dsn(), args.database)) as conn:
            for pack in packs:
                print(json.dumps(install(conn, pack)))
    except PackError as exc:
        sys.exit(f"pack error: {exc}")


if __name__ == "__main__":
    main()

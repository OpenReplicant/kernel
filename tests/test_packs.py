"""Packs: manifests load and validate, every pack installs beside the others, reinstalling
changes nothing, upgrades only add or relabel, conflicts are refused with the reason, the
kernel range is enforced, and a pack's terms exist only where it is installed."""

from __future__ import annotations

import copy
import importlib.metadata
from pathlib import Path
from typing import Any

import psycopg
import pytest

from kernel import admin
from kernel import packs as packs_mod
from kernel.packs import PackError
from kernel.testing import KernelDB, Rejected


@pytest.fixture(scope="module")
def wmk_packs() -> tuple[str, ...]:
    """This module's databases start with the kernel alone."""
    return ()


def manifest(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "toy",
        "version": "0.1.0",
        "kernel": ">=0.2 <1.0",
        "namespaces": [{"name": "toy", "label": "Toy", "description": "A test namespace."}],
        "kinds": [
            {"name": "widget", "node_type": "Entity", "label": "Widget", "description": "A test kind."}
        ],
        "edge_kinds": [
            {"name": "holds", "edge": "part_of", "label": "holds", "description": "A test edge kind."}
        ],
        "rules": [
            {
                "id": "toy.allowed_kinds",
                "category": "types",
                "namespace": "toy",
                "params": {"kinds": ["widget", "concept"]},
                "label": "Toy kinds",
                "description": "The toy namespace allows widgets and concepts.",
            }
        ],
    }
    base.update(overrides)
    return base


def install(dbname: str, m: dict[str, Any]) -> dict[str, Any]:
    with psycopg.connect(admin.dsn_for(admin.admin_dsn(), dbname)) as conn:
        return packs_mod.install_manifest(conn, m)


def test_kernel_version_matches_the_package(kdb: KernelDB) -> None:
    assert kdb.one("SELECT kernel.version()") == importlib.metadata.version("world-model-kernel")


def test_every_pack_loads_and_installs_beside_the_others(dbname: str) -> None:
    found = packs_mod.discover()
    assert {"bpm-reference", "research"} <= set(found)
    first = admin.install_packs(dbname, list(found))
    assert [r["status"] for r in first] == ["installed"] * len(found)
    again = admin.install_packs(dbname, list(found))
    assert [r["status"] for r in again] == ["unchanged"] * len(found)


def test_a_pack_term_exists_only_where_the_pack_is_installed(kdb: KernelDB, dbname: str) -> None:
    agent = kdb.register()
    write = {"op": "create", "ref": "$p", "kind": "paper", "namespace": "research", "name": "A paper"}
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "A paper exists", [write], basis="observed")
    assert err.value.problem == "types"
    admin.install_packs(dbname, ["research"])
    assert kdb.claim(agent, "A paper exists", [write], basis="observed")["refs"]["$p"].startswith("ent_")


def test_upgrades_add_and_relabel(dbname: str, kdb: KernelDB) -> None:
    assert install(dbname, manifest())["status"] == "installed"
    newer = manifest(version="0.2.0")
    newer["kinds"] = [
        {**newer["kinds"][0], "label": "Gadget"},
        {"name": "sprocket", "node_type": "Entity", "label": "Sprocket", "description": "Another kind."},
    ]
    result = install(dbname, newer)
    assert result["status"] == "updated" and result["kinds"] == 2
    assert kdb.one("SELECT label FROM kernel.kinds WHERE name = 'widget'") == "Gadget"
    assert kdb.one("SELECT defined_by FROM kernel.kinds WHERE name = 'sprocket'") == "toy"
    assert kdb.one("SELECT version FROM kernel.packs WHERE name = 'toy'") == "0.2.0"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda m: m["kinds"].append(
                {"name": "role", "node_type": "Entity", "label": "R", "description": "d"}
            ),
            "kind role is defined by core",
        ),
        (
            lambda m: m["kinds"].append(
                {"name": "note", "node_type": "Claim", "label": "N", "description": "d"}
            ),
            "Claim kinds are modalities",
        ),
        (
            lambda m: m["edge_kinds"].append(
                {"name": "x", "edge": "befriends", "label": "x", "description": "d"}
            ),
            "befriends is not a kernel edge",
        ),
        (
            lambda m: m["rules"].append({**m["rules"][0], "id": "other.rule"}),
            "ids start with one of the pack's namespaces",
        ),
        (
            lambda m: m["rules"].append({**m["rules"][0], "id": "toy.more", "params": {"kinds": ["gizmo"]}}),
            "gizmo is not a kind",
        ),
        (
            lambda m: m["rules"].append(
                {
                    "id": "toy.ids",
                    "category": "identity",
                    "label": "I",
                    "description": "d",
                    "params": {
                        "node_type": "Entity",
                        "kinds": ["widget"],
                        "keys": ["serial"],
                        "patterns": {"serial": "(["},
                    },
                }
            ),
            "is not a valid pattern",
        ),
        (
            lambda m: m["rules"].append(
                {
                    "id": "toy.holds",
                    "category": "domain_range",
                    "label": "H",
                    "description": "d",
                    "params": {"edge": "part_of", "kind": "reads_into"},
                }
            ),
            "reads_into is not an edge kind of part_of",
        ),
    ],
)
def test_conflicts_and_unknown_terms_are_refused(dbname: str, change: Any, reason: str) -> None:
    bad = copy.deepcopy(manifest())
    change(bad)
    with pytest.raises(PackError, match=reason):
        install(dbname, bad)


def test_another_packs_terms_are_refused(dbname: str) -> None:
    install(dbname, manifest())
    rival = manifest(
        name="rival",
        namespaces=[{"name": "rival", "label": "R", "description": "d"}],
        rules=[],
        edge_kinds=[],
    )
    with pytest.raises(PackError, match="kind widget is defined by toy"):
        install(dbname, rival)


def test_upgrades_may_not_drop_or_retype(dbname: str) -> None:
    install(dbname, manifest())
    with pytest.raises(PackError, match="removing kind widget is not supported"):
        install(dbname, manifest(version="0.2.0", kinds=[], rules=[]))
    retyped = manifest(version="0.2.0")
    retyped["kinds"][0]["node_type"] = "Event"
    with pytest.raises(PackError, match="kind widget is a Entity; it cannot become a Event"):
        install(dbname, retyped)


def write_pack(folder: Path, frontmatter: str, schema: str = "", rules: str = "") -> Path:
    folder.mkdir()
    (folder / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n# Pack\n")
    if schema:
        (folder / "schema.yaml").write_text(schema)
    if rules:
        (folder / "rules.yaml").write_text(rules)
    return folder


def test_loader_validates_folders(tmp_path: Path) -> None:
    meta = 'name: toy\ndescription: d\nmetadata:\n  version: "0.1.0"\n  kernel: ">=0.2 <1.0"\n'
    good = write_pack(
        tmp_path / "good", meta, "kinds:\n  - {name: widget, node_type: Entity, label: W, description: d}\n"
    )
    assert packs_mod.load(good).manifest["kinds"][0]["name"] == "widget"
    missing = write_pack(
        tmp_path / "missing", meta, "kinds:\n  - {name: widget, node_type: Entity, label: W}\n"
    )
    with pytest.raises(PackError, match=r"kinds\[0\] is missing description"):
        packs_mod.load(missing)
    unknown = write_pack(tmp_path / "unknown", meta, "classes: []\n")
    with pytest.raises(PackError, match="unknown sections classes"):
        packs_mod.load(unknown)
    no_range = write_pack(tmp_path / "norange", 'name: toy\ndescription: d\nmetadata:\n  version: "0.1.0"\n')
    with pytest.raises(PackError, match=r"metadata\.kernel"):
        packs_mod.load(no_range)
    with_sql = write_pack(tmp_path / "withsql", meta)
    (with_sql / "sql").mkdir()
    with pytest.raises(PackError, match="pack SQL is not supported"):
        packs_mod.load(with_sql)


def test_the_kernel_range_is_enforced(tmp_path: Path, dbname: str) -> None:
    future = write_pack(
        tmp_path / "future",
        'name: future\ndescription: d\nmetadata:\n  version: "1.0.0"\n  kernel: ">=9.0"\n',
    )
    with (
        psycopg.connect(admin.dsn_for(admin.admin_dsn(), dbname)) as conn,
        pytest.raises(PackError, match=r"supports kernel >=9\.0, not 0\.4\.0"),
    ):
        packs_mod.install(conn, packs_mod.load(future))

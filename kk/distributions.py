"""kk distribution list: the distributions in this repository (D-84, D-118).

A distribution is a preset bundle of modules, packs, skills and views that does one job
(distributions/<name>/distribution.yaml, kernel/schemas/distribution.schema.json). `kk up --distribution
NAME` starts it as its own stack: its own compose project and env file, the listed modules installed
(their migrations applied where this kernel lacks their agent), the kernel serving the parts it names, and
the UI listing only its views. Nothing in the kernel changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def names() -> list[str]:
    return sorted(p.parent.name for p in ROOT.glob("distributions/*/distribution.yaml"))


def load(name: str) -> dict:
    path = ROOT / "distributions" / name / "distribution.yaml"
    if not path.exists():
        raise SystemExit(f"no distribution {name!r}; `kk distribution list` has {', '.join(names())}")
    return yaml.safe_load(path.read_text())


def current() -> dict | None:
    """The distribution this process runs for (KK_DISTRIBUTION), or None."""
    return load(os.environ["KK_DISTRIBUTION"]) if os.environ.get("KK_DISTRIBUTION") else None


def ensure(conn, dist: dict) -> list[str]:
    """Apply the migration of each listed module whose agent this kernel lacks (as `kk module install` does);
    the modules applied. A second run applies none."""
    applied = []
    for name in dist["modules"]:
        folder = ROOT / "modules" / name
        manifest = yaml.safe_load((folder / "module.yaml").read_text())
        migration = folder / manifest.get("migration", "migration.sql")
        agent = (manifest.get("agent") or {}).get("iri")
        if migration.exists() and not conn.execute("select 1 from kernel.agent where iri = %s", (agent,)).fetchone():
            conn.execute("set client_min_messages = warning")
            conn.execute(migration.read_text())
            conn.execute("reset role")
            applied.append(name)
    return applied


def main(argv: list[str]) -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="kk distribution", description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("list",))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ds = [load(n) for n in names()]
    if a.json:
        print(json.dumps(ds, indent=2))
        return
    for d in ds:
        print(f"{d['name']:<13} {d['does']}\n{'':<13} modules {len(d['modules'])}, packs {len(d['packs'])}, "
              f"views {', '.join(d['views']) or '-'}, demo {d['demo']}")

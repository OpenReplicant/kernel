"""Validate every module manifest.

Checks:
  * modules/*/module.yaml against kernel/schemas/module.schema.json
  * slot ids (filled and required) exist in ontology/slots.yaml
  * files a manifest points to (skill, migration) exist
  * distributions/*/distribution.yaml (and any file given as an argument) against
    kernel/schemas/distribution.schema.json: every module, view and pack it names exists, views are view
    modules, skills are modules that carry a skill, and the name is its folder's

Run: python scripts/check_manifests.py   (needs pyyaml, jsonschema)
"""
import json
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent


def check_distribution(path: Path, schema: dict, modules: dict, packs: set[str]) -> list[str]:
    try:
        d = yaml.safe_load(path.read_text())
        jsonschema.validate(d, schema)
    except Exception as e:  # noqa: BLE001
        return [f"{path}: {e}"]
    errors = []
    if path.parent.name != d["name"] and path.parent.name != "distributions":
        errors.append(f"{path}: name {d['name']!r} is not its folder's")
    for m in d["modules"]:
        if m not in modules:
            errors.append(f"{path}: module {m!r} is not in modules/")
    for key in ("views", "skills"):
        for m in d[key]:
            if m not in d["modules"]:
                errors.append(f"{path}: {key[:-1]} {m!r} is not among its modules")
            elif m in modules and (modules[m].get("runtime", {}).get("type") != "view" if key == "views" else "skill" not in modules[m]):
                errors.append(f"{path}: {m!r} is not a {'view module' if key == 'views' else 'module with a skill'}")
    errors += [f"{path}: pack {p!r} is not in ontology/packs.yaml" for p in d["packs"] if p not in packs]
    return errors


def main() -> int:
    module_schema = json.loads((ROOT / "kernel/schemas/module.schema.json").read_text())
    slots = yaml.safe_load((ROOT / "ontology/slots.yaml").read_text())
    slot_ids = [s["id"] for group in slots["groups"].values() for s in group]
    errors: list[str] = []

    if len(slot_ids) != len(set(slot_ids)):
        errors.append("ontology/slots.yaml: duplicate slot ids")
    kinds = set(module_schema["properties"]["kind"]["enum"])
    for group in slots["groups"].values():
        for s in group:
            if s["kind"] not in kinds:
                errors.append(f"slot {s['id']}: unknown kind {s['kind']}")

    for manifest_path in sorted(ROOT.glob("modules/*/module.yaml")):
        rel = manifest_path.relative_to(ROOT)
        mdir = manifest_path.parent
        try:
            m = yaml.safe_load(manifest_path.read_text())
            jsonschema.validate(m, module_schema)
        except Exception as e:  # noqa: BLE001 - report and continue
            errors.append(f"{rel}: {e}")
            continue
        for slot in m["slots"] + [r["slot"] for r in m.get("requires", [])]:
            if slot not in slot_ids:
                errors.append(f"{rel}: unknown slot '{slot}'")
        for key in ("skill", "migration"):
            if key in m and not (mdir / m[key]).exists():
                errors.append(f"{rel}: {key} file {m[key]} missing")

    modules = {p.parent.name: yaml.safe_load(p.read_text()) for p in ROOT.glob("modules/*/module.yaml")}
    registry = yaml.safe_load((ROOT / "ontology/packs.yaml").read_text())
    packs = {p["id"] for tier in ("core", "profile", "format") for p in registry.get(tier, [])}
    dschema = json.loads((ROOT / "kernel/schemas/distribution.schema.json").read_text())
    files = sorted(ROOT.glob("distributions/*/distribution.yaml")) + [Path(a) for a in sys.argv[1:]]
    for f in files:
        errors += check_distribution(f, dschema, modules, packs)

    for e in errors:
        print("ERROR", e)
    print(f"{len(files)} distributions, {len(slot_ids)} slots, {len(list(ROOT.glob('modules/*/module.yaml')))} manifests checked, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

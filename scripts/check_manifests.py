"""Validate every module manifest and chain definition.

Checks:
  * modules/*/module.yaml against kernel/schemas/module.schema.json
  * every chain file (listed or present) against kernel/schemas/chain.schema.json
  * slot ids (filled and required) exist in ontology/slots.yaml
  * chain routing targets exist; a chain runs as its module's agent
  * files a manifest points to (chains, skill, migration) exist

Run: python scripts/check_manifests.py   (needs pyyaml, jsonschema)
"""
import json
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    module_schema = json.loads((ROOT / "kernel/schemas/module.schema.json").read_text())
    chain_schema = json.loads((ROOT / "kernel/schemas/chain.schema.json").read_text())
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
        listed = set()
        for c in m.get("runtime", {}).get("chains", []):
            listed.add((mdir / c).resolve())
            if not (mdir / c).exists():
                errors.append(f"{rel}: chain {c} missing")
        for chain_path in sorted(mdir.glob("chains/*.yaml")):
            crel = chain_path.relative_to(ROOT)
            if chain_path.resolve() not in listed:
                errors.append(f"{crel}: not listed in runtime.chains")
            try:
                ch = yaml.safe_load(chain_path.read_text())
                jsonschema.validate(ch, chain_schema)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{crel}: {e}")
                continue
            step_ids = {s["id"] for s in ch["steps"]}
            for s in ch["steps"]:
                for target in s.get("next", {}).values():
                    for t in [target] if isinstance(target, str) else target:
                        if t not in step_ids:
                            errors.append(f"{crel}: step {s['id']} routes to unknown step {t}")
            if ch["runs_as"] != m["agent"]["iri"]:
                errors.append(f"{crel}: runs_as {ch['runs_as']} is not the module agent {m['agent']['iri']}")

    for e in errors:
        print("ERROR", e)
    print(f"{len(slot_ids)} slots, {len(list(ROOT.glob('modules/*/module.yaml')))} manifests checked, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

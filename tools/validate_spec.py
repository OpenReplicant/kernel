#!/usr/bin/env python3
"""Validate a paper spec.yaml: JSON Schema first, then cross-reference rules
the schema can't express. Exit code 1 on any error.

Usage: validate_spec.py path/to/spec.yaml [--schema path/to/spec.schema.json]
"""
import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE.parent / "schema" / "spec.schema.json"

# Primitives the model must not be able to skip or invoke at will.
INTERNAL_SLOTS = {"evaluator", "reflector", "refiner", "controller",
                  "search", "aggregator", "curriculum", "context_manager"}


def cross_checks(spec):
    errors, warnings = [], []
    slot_ids = [s["id"] for s in spec["slots"]]
    asset_by_id = {a["id"]: a for a in spec.get("assets", [])}
    model_roles = {m["role"] for m in spec["models"]}

    for dup in {i for i in slot_ids if slot_ids.count(i) > 1}:
        errors.append(f"duplicate slot id: {dup}")

    for s in spec["slots"]:
        impl = s["implementation"]
        for a in s.get("uses_assets", []):
            if a not in asset_by_id:
                errors.append(f"slot {s['id']}: unknown asset '{a}'")
        if s.get("model_role") and s["model_role"] not in model_roles:
            errors.append(f"slot {s['id']}: unknown model_role '{s['model_role']}'")
        if impl["kind"] == "mcp_tool":
            a = asset_by_id.get(impl.get("asset"))
            if not a or a["kind"] != "mcp_server":
                errors.append(f"slot {s['id']}: mcp_tool must reference an mcp_server asset")
            if s["slot"] in INTERNAL_SLOTS:
                errors.append(f"slot {s['id']}: internal primitive '{s['slot']}' bound as mcp_tool "
                              "(the model could skip or trigger it at will)")
        if impl["source"] == "paper_code" and not any(
                src["kind"] == "repo" for src in spec["paper"]["sources"]):
            errors.append(f"slot {s['id']}: source paper_code but no repo in paper.sources")

    for w in spec["wiring"]:
        for end in ("from", "to"):
            sid = w[end].split(".")[0]
            if sid not in slot_ids:
                errors.append(f"wiring {w['from']} -> {w['to']}: unknown slot '{sid}'")

    for t in spec["behavior_tests"]:
        for ev in [t["given"], *t["expect"]]:
            for key in ("slot", "contains_output_of"):
                if ev.get(key) and ev[key] not in slot_ids:
                    errors.append(f"behavior test {t['id']}: unknown slot '{ev[key]}'")
        if t["claim"] not in spec["mechanism"]["claims"]:
            warnings.append(f"behavior test {t['id']}: claim text not found in mechanism.claims")

    harness = spec["evaluation"].get("harness_asset")
    if harness and asset_by_id.get(harness, {}).get("kind") != "eval_harness":
        errors.append(f"evaluation.harness_asset '{harness}' is not an eval_harness asset")

    for src in spec["paper"]["sources"]:
        if src["kind"] == "repo" and set(src.get("commit", "")) <= {"0"}:
            warnings.append(f"repo {src['ref']} is not pinned to a real commit")

    # Provenance summary for SPEC.md's "unverified fields" line.
    counts = {"inferred": 0, "defaulted": 0}

    def walk(node):
        if isinstance(node, dict):
            if node.get("provenance") in counts:
                counts[node["provenance"]] += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(spec)
    return errors, warnings, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    args = ap.parse_args()

    schema = json.loads(Path(args.schema).read_text())
    spec = yaml.safe_load(Path(args.spec).read_text())

    schema_errors = sorted(Draft202012Validator(schema).iter_errors(spec), key=lambda e: list(e.path))
    for e in schema_errors:
        print(f"SCHEMA  {'/'.join(map(str, e.path)) or '<root>'}: {e.message}")
    if schema_errors:
        sys.exit(1)

    errors, warnings, counts = cross_checks(spec)
    for e in errors:
        print(f"ERROR   {e}")
    for w in warnings:
        print(f"WARN    {w}")
    print(f"OK schema · {len(errors)} errors · {len(warnings)} warnings · "
          f"unverified: {counts['inferred']} inferred, {counts['defaulted']} defaulted")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

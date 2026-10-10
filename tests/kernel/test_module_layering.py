"""Layering (docs/ARCHITECTURE.md): the kernel imports no module; a module imports only the
kernel, pclib, itself and the modules it depends on (directly or transitively); dependencies
point down the layers."""
import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIRST_PARTY = {"kernel", "pclib", "modules", "scripts", "tools", "tests"}


def manifests():
    return {p.parent.name: yaml.safe_load(p.read_text()) for p in (ROOT / "modules").glob("*/module.yaml")}


def closure(ms, name):
    """Everything a module depends on, directly or through its dependencies."""
    out, todo = set(), list(ms[name].get("depends", []))
    while todo:
        d = todo.pop()
        if d not in out:
            out.add(d)
            todo.extend(ms[d].get("depends", []))
    return out


def imports(py: Path):
    for node in ast.walk(ast.parse(py.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_modules_import_only_what_they_declare():
    ms = manifests()
    assert {"systems", "agent_design", "evaluation", "paper_compiler"} <= set(ms)
    for name, m in ms.items():
        allowed = {"kernel", "pclib", f"modules.{name}"} | {f"modules.{d}" for d in closure(ms, name)}
        for py in (ROOT / "modules" / name).rglob("*.py"):
            for mod in imports(py):
                top = mod.split(".")[0]
                if top not in FIRST_PARTY:
                    continue
                key = ".".join(mod.split(".")[:2]) if top == "modules" else top
                assert key in allowed, f"{py.relative_to(ROOT)} imports {mod}; {name} declares {m.get('depends')}"


def test_dependencies_point_down_the_layers():
    ms = manifests()
    for name, m in ms.items():
        for d in m.get("depends", []):
            assert d in ms, f"{name} depends on unknown module {d}"
            assert ms[d]["layer"] <= m["layer"], f"{name} (L{m['layer']}) depends on {d} (L{ms[d]['layer']})"


def test_kernel_imports_no_module():
    for py in (ROOT / "kernel").rglob("*.py"):
        for mod in imports(py):
            assert mod.split(".")[0] not in ("modules", "pclib", "scripts", "tools"), f"{py}: {mod}"

"""The kernel is domain-free: it never imports applications, and never names a domain."""
import ast
import re
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[2] / "kernel"
# Words that would mean domain knowledge leaked into the kernel.
FORBIDDEN = re.compile(r"\b(papers?|slots?|reflexion|mechanism|benchmark|agent[_ -]design)\b|\bad:", re.I)


def test_kernel_does_not_import_apps():
    for py in KERNEL.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in ("apps", "registry", "pclib", "scripts"), f"{py}: imports {n}"


def test_kernel_names_no_domain():
    for py in KERNEL.rglob("*.py"):
        for i, line in enumerate(py.read_text().splitlines(), 1):
            assert not FORBIDDEN.search(line), f"{py.name}:{i}: {line.strip()}"

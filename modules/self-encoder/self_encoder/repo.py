"""Any repository (M3-T2, D-68): packages and their dependencies, documents' headings. Deterministic
parsers, standard library only; every claim references its file and line span (Builder).

  pyproject.toml          [project] (PEP 621) or [tool.poetry]: name, version, dependencies, optional ones
  requirements*.txt       pinned or ranged requirements (PEP 508): the file requires them, and is part of
                          the package in the same folder (else the repository)
  package.json            name, version, dependencies (+ peer); dev and optional ones as suggestions
  go.mod                  module path, go version, require
  Cargo.toml              [package], [dependencies] (+ build); dev ones as suggestions
  *.md                    the document and its headings (#, ##, ###), outside code fences

A package is its Package URL (pkg:pypi/..., pkg:npm/..., pkg:golang/..., pkg:cargo/...), typed
schema:SoftwareSourceCode and dcterms:isPartOf the repository (urn:kk:repo:<name>); a dependency is
schema:softwareRequirements (or schema:softwareSuggestions) the required package, with the version range
as written in the qualifier schema:version.
"""
from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from urllib.parse import quote

S = "http://schema.org/"
SOURCE_CODE, REQUIRES, SUGGESTS, LANGUAGE, VERSION = (S + t for t in (
    "SoftwareSourceCode", "softwareRequirements", "softwareSuggestions", "programmingLanguage", "version"))
MANIFESTS = {"pyproject.toml", "package.json", "go.mod", "Cargo.toml"}
REQUIREMENTS = re.compile(r"(^|/)requirements[\w.-]*\.txt$")
SKIP_DIRS = {".git", "fixtures", "node_modules", "vendor", "target", "dist", "build", ".venv", "venv", "__pycache__"}
MAX_BYTES = 512 * 1024
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def repo_name(repo: Path, name: str | None = None) -> str:
    """The repository's name: as given (kk up passes the project folder's), else its folder's."""
    return name or repo.resolve().name


def repo_iri(repo: Path, name: str | None = None) -> str:
    return f"urn:kk:repo:{quote(repo_name(repo, name), safe='')}"


def commit(repo: Path) -> str | None:
    """The checked-out commit, when the repository is a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def purl(kind: str, name: str) -> str:
    if kind == "pypi":
        name = re.sub(r"[-_.]+", "-", name).lower()
    return f"pkg:{kind}/" + "/".join(quote(p, safe="") for p in name.split("/"))


def wanted(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return (name in MANIFESTS or bool(REQUIREMENTS.search(rel)) or name.lower().endswith(".md")) \
        and not any(p in SKIP_DIRS or p.startswith(".") for p in PurePosixPath(rel).parts[:-1])


def list_repo_files(repo: Path) -> list[str]:
    """Manifests and Markdown files: tracked files when the repository is a git checkout (so .gitignore
    applies), otherwise every file outside build and dependency folders; at most MAX_BYTES each."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True, timeout=60)
        files = [f for f in out.stdout.decode().split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        files = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()]
    return sorted(f for f in files if wanted(f) and (repo / f).is_file() and (repo / f).stat().st_size <= MAX_BYTES)


def line_of(lines: list[str], needle: str, start: int = 0) -> tuple[int, int]:
    """The first line at or after `start` (0-based) containing `needle`, as a 1-based span."""
    for i in range(start, len(lines)):
        if needle in lines[i]:
            return (i + 1, i + 1)
    return (1, max(1, len(lines)))


def section(lines: list[str], header: str) -> int:
    """The 0-based line of a TOML table header such as [project], or 0."""
    for i, line in enumerate(lines):
        if line.strip() == header:
            return i
    return 0


def package(b, repo: str, pkg: str, name: str, version, language: str, span, lines) -> None:
    b.claim(pkg, "rdf:type", SOURCE_CODE, span)
    b.claim(pkg, "rdfs:label", name, span)
    b.claim(pkg, "dcterms:isPartOf", repo, span)
    b.claim(pkg, LANGUAGE, language, span)
    if version:
        b.claim(pkg, "owl:versionInfo", str(version), line_of(lines, str(version), span[0] - 1))


def dependency(b, pkg: str, dep: str, spec: str, span, optional: bool = False) -> None:
    spec = spec.strip()
    b.claim(pkg, SUGGESTS if optional else REQUIRES, dep, span, {VERSION: spec} if spec else None)


def pep508(req: str) -> tuple[str, str] | None:
    m = PEP508.match(req.split(";")[0])
    return (m.group(1), m.group(3).strip()) if m else None


def encode_pyproject(b, repo: str, text: str) -> None:
    lines, d = text.splitlines(), tomllib.loads(text)
    project, poetry = d.get("project") or {}, (d.get("tool") or {}).get("poetry") or {}
    name = project.get("name") or poetry.get("name")
    if not name:
        return
    pkg = purl("pypi", name)
    start = section(lines, "[project]") if project else section(lines, "[tool.poetry]")
    package(b, repo, pkg, name, project.get("version") or poetry.get("version"), "Python",
            line_of(lines, "name", start), lines)
    for req in project.get("dependencies", []):
        if (p := pep508(req)) and p[0]:
            dependency(b, pkg, purl("pypi", p[0]), p[1], line_of(lines, req, start))
    for group, reqs in (project.get("optional-dependencies") or {}).items():
        for req in reqs:
            if (p := pep508(req)) and p[0]:
                dependency(b, pkg, purl("pypi", p[0]), p[1], line_of(lines, req, start), optional=True)
    for table, optional in (("dependencies", False), ("dev-dependencies", True)):
        top = section(lines, f"[tool.poetry.{table}]")
        for dep, spec in (poetry.get(table) or {}).items():
            if dep.lower() == "python":
                continue
            spec = spec.get("version", "") if isinstance(spec, dict) else str(spec)
            dependency(b, pkg, purl("pypi", dep), spec, line_of(lines, dep, top), optional)


def encode_requirements(b, repo: str, text: str, rel: str, owner: str) -> None:
    lines = text.splitlines()
    req = f"{repo}/{quote(rel)}"
    whole = (1, max(1, len(lines)))
    b.claim(req, "rdf:type", SOURCE_CODE, whole)
    b.claim(req, "rdfs:label", rel, whole)
    b.claim(req, "dcterms:isPartOf", owner, whole)
    for i, line in enumerate(lines):
        line = line.split(" #")[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http:", "https:", "./", "../")):
            continue
        if p := pep508(line):
            dependency(b, req, purl("pypi", p[0]), p[1], (i + 1, i + 1))


def encode_package_json(b, repo: str, text: str) -> None:
    lines, d = text.splitlines(), json.loads(text)
    if not isinstance(d, dict) or not d.get("name"):
        return
    pkg = purl("npm", d["name"])
    package(b, repo, pkg, d["name"], d.get("version"), "JavaScript", line_of(lines, '"name"'), lines)
    for table, optional in (("dependencies", False), ("peerDependencies", False),
                            ("devDependencies", True), ("optionalDependencies", True)):
        top = line_of(lines, f'"{table}"')[0] - 1
        for dep, spec in (d.get(table) or {}).items():
            dependency(b, pkg, purl("npm", dep), str(spec), line_of(lines, f'"{dep}"', top), optional)


def encode_go_mod(b, repo: str, text: str) -> None:
    lines = text.splitlines()
    mod = next((ln.split()[1] for ln in lines if ln.startswith("module ") and len(ln.split()) > 1), None)
    if not mod:
        return
    pkg = purl("golang", mod)
    go = next((ln.split()[1] for ln in lines if ln.startswith("go ") and len(ln.split()) > 1), None)
    package(b, repo, pkg, mod, None, "Go", line_of(lines, "module "), lines)
    block = False
    for i, ln in enumerate(lines):
        s = ln.split("//")[0].strip()
        if s.startswith("require ("):
            block = True
            continue
        if block and s == ")":
            block = False
            continue
        parts = s[len("require "):].split() if s.startswith("require ") else s.split() if block else []
        if len(parts) >= 2:
            dependency(b, pkg, purl("golang", parts[0]), parts[1], (i + 1, i + 1))
    if go:
        b.claim(pkg, LANGUAGE, f"Go {go}", line_of(lines, "go "))


def encode_cargo(b, repo: str, text: str) -> None:
    lines, d = text.splitlines(), tomllib.loads(text)
    p = d.get("package") or {}
    if not p.get("name"):
        return
    pkg = purl("cargo", p["name"])
    version = p.get("version") if isinstance(p.get("version"), str) else None
    package(b, repo, pkg, p["name"], version, "Rust", line_of(lines, "name", section(lines, "[package]")), lines)
    for table, optional in (("dependencies", False), ("build-dependencies", False), ("dev-dependencies", True)):
        top = section(lines, f"[{table}]")
        for dep, spec in (d.get(table) or {}).items():
            spec = spec.get("version", "") if isinstance(spec, dict) else str(spec)
            dependency(b, pkg, purl("cargo", dep), spec, line_of(lines, dep, top), optional)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def encode_markdown(b, repo: str, text: str, rel: str) -> None:
    lines = text.splitlines()
    doc = f"{repo}/{quote(rel)}"
    b.claim(doc, "dcterms:isPartOf", repo, (1, max(1, len(lines))))
    fence, title = False, None
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        m = None if fence else HEADING.match(line)
        if not m:
            continue
        heading = re.sub(r"[*_`]", "", m.group(2)).strip()
        if not heading:
            continue
        if len(m.group(1)) == 1 and title is None:
            title = heading
            b.claim(doc, "dcterms:title", heading, (i, i))
            continue
        part = f"{doc}#{slug(heading)}"
        b.claim(part, "rdfs:label", heading, (i, i))
        b.claim(part, "dcterms:isPartOf", doc, (i, i))
    if title is None:
        b.claim(doc, "dcterms:title", rel, (1, max(1, len(lines))))


def encode(b, repo_dir: Path, rel: str, text: str, name: str | None = None) -> None:
    """Dispatch one repository file to its encoder; the repository itself is typed from its root files."""
    repo = repo_iri(repo_dir, name)
    file = PurePosixPath(rel).name
    if "/" not in rel:
        b.claim(repo, "rdf:type", SOURCE_CODE, (1, 1))
        b.claim(repo, "rdfs:label", repo_name(repo_dir, name), (1, 1))
    if file == "pyproject.toml":
        encode_pyproject(b, repo, text)
    elif REQUIREMENTS.search(rel):
        here = repo_dir / PurePosixPath(rel).parent / "pyproject.toml"
        owner = repo
        if here.is_file():
            d = tomllib.loads(here.read_text())
            n = (d.get("project") or {}).get("name") or ((d.get("tool") or {}).get("poetry") or {}).get("name")
            owner = purl("pypi", n) if n else repo
        encode_requirements(b, repo, text, rel, owner)
    elif file == "package.json":
        encode_package_json(b, repo, text)
    elif file == "go.mod":
        encode_go_mod(b, repo, text)
    elif file == "Cargo.toml":
        encode_cargo(b, repo, text)
    elif file.lower().endswith(".md"):
        encode_markdown(b, repo, text, rel)

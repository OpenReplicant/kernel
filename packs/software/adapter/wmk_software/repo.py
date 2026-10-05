"""Reading a repository: the files the adapter maps, from the working tree or at a commit, and
the first-parent git history as text. Read-only: git is only asked to show and list."""

from __future__ import annotations

import fnmatch
import posixpath
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
WORKFLOWS = ".github/workflows/"
HISTORY_FILE = "git-history.txt"


class RepoError(Exception):
    """A folder that is not a git repository, or a revision that does not exist."""


@dataclass(frozen=True)
class Repository:
    url: str
    name: str
    commit: str
    branch: str
    files: dict[str, str]
    history: str


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RepoError(f"git {' '.join(args[:2])} failed in {root}: {detail.strip()}") from None


def normalize_url(remote: str) -> str:
    """A browsable URL for a git remote, without credentials or a .git suffix:
    git@github.com:owner/repo.git becomes https://github.com/owner/repo."""
    scp = re.match(r"^[\w.-]+@([\w.-]+):(?!//)(.+)$", remote)
    if scp:
        remote = f"https://{scp.group(1)}/{scp.group(2)}"
    parts = urlsplit(remote)
    host = parts.hostname or ""
    if parts.port and parts.scheme not in ("ssh", "git"):
        host += f":{parts.port}"
    scheme = "https" if parts.scheme in ("ssh", "git", "") else parts.scheme
    path = re.sub(r"\.git$", "", parts.path.rstrip("/"))
    return urlunsplit((scheme, host, path, "", ""))


def repo_name(url: str) -> str:
    """owner/repo from a repository URL (the last two path segments)."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return "/".join(segments[-2:]) if segments else url


def file_uri(url: str, path: str) -> str:
    """A stable link to a file on the default branch (stable across commits, so an unchanged
    file is recognised as already ingested)."""
    host = urlsplit(url).hostname or ""
    return f"{url}/blob/HEAD/{path}" if host in ("github.com", "gitlab.com") else f"{url}#{path}"


def read(
    root: Path | str,
    *,
    rev: str | None = None,
    url: str | None = None,
    branch: str | None = None,
    history: int = 50,
) -> Repository:
    """The repository at `root`: at commit `rev`, or its working tree (tracked and untracked,
    unignored files) when `rev` is None."""
    root = Path(root)
    git(root, "rev-parse", "--git-dir")
    commit = git(root, "rev-parse", rev or "HEAD").strip()
    if url is None:
        remote = git(root, "config", "--get", "remote.origin.url").strip() if _has_origin(root) else ""
        url = normalize_url(remote) if remote else root.resolve().as_uri()
    if branch is None:
        branch = rev.split("/")[-1] if rev else git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if rev:
        paths = set(git(root, "ls-tree", "-r", "--name-only", commit).splitlines())
    else:
        paths = set(git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines())

    def content(path: str) -> str:
        if rev:
            return git(root, "show", f"{commit}:{path}")
        return (root / path).read_text()

    files = {p: content(p) for p in wanted(paths, content)}
    return Repository(
        url=url,
        name=repo_name(url),
        commit=commit,
        branch=branch,
        files=files,
        history=history_text(root, url, commit, branch, history),
    )


def _has_origin(root: Path) -> bool:
    return "origin" in git(root, "remote").split()


def wanted(paths: set[str], content: Any) -> list[str]:
    """The files the adapter maps, in mapping order: pyproject.toml and the uv workspace
    members' pyproject.toml, the compose file and the Dockerfiles it builds, CI workflows,
    Kubernetes manifests and OpenAPI documents."""
    out: list[str] = []
    if "pyproject.toml" in paths:
        out.append("pyproject.toml")
        root_project = tomllib.loads(content("pyproject.toml"))
        members = root_project.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
        dirs = sorted({posixpath.dirname(p) for p in paths if p.endswith("/pyproject.toml")})
        out += [f"{d}/pyproject.toml" for d in dirs if any(fnmatch.fnmatch(d, m) for m in members)]
    compose = next((c for c in COMPOSE_FILES if c in paths), None)
    if compose:
        out.append(compose)
        out += [d for d in dockerfiles(yaml.safe_load(content(compose)) or {}) if d in paths]
    out += sorted(p for p in paths if p.startswith(WORKFLOWS) and p.endswith((".yml", ".yaml")))
    # Kubernetes manifests and OpenAPI documents, wherever they are (not compose or workflows).
    from wmk_software.manifests import manifest_type

    taken = set(out)
    out += sorted(
        p
        for p in paths
        if p not in taken
        and not p.startswith(WORKFLOWS)
        and p.endswith((".yaml", ".yml", ".json"))
        and manifest_type(p, content(p))
    )
    return list(dict.fromkeys(out))


def dockerfiles(compose: dict[str, Any]) -> list[str]:
    """Repository paths of the Dockerfiles a compose file builds, in service order."""
    out = []
    for service in (compose.get("services") or {}).values():
        build = build_spec(service or {})
        if build:
            out.append(build[1])
    return list(dict.fromkeys(out))


def build_spec(service: dict[str, Any]) -> tuple[str, str] | None:
    """(context, dockerfile path in the repository) for a service that builds an image."""
    build = service.get("build")
    if not build:
        return None
    if isinstance(build, str):
        build = {"context": build}
    context = str(build.get("context", "."))
    if "://" in context:
        return None
    dockerfile = str(build.get("dockerfile", "Dockerfile"))
    return context, posixpath.normpath(posixpath.join(context, dockerfile))


def history_text(root: Path, url: str, commit: str, branch: str, limit: int) -> str:
    """The first-parent history, newest first, one commit per line: hash, UTC time, subject."""
    log = git(root, "log", "--first-parent", f"--max-count={limit}", "--format=%H %ct %s", commit)
    lines = [f"Repository: {url}", f"Branch: {branch}", "", "First-parent commits, newest first:", ""]
    for line in log.splitlines():
        sha, stamp, subject = [*line.split(" ", 2), ""][:3]
        when = datetime.fromtimestamp(int(stamp), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"{sha} {when} {subject}".rstrip())
    return "\n".join(lines) + "\n"


def parse_history(text: str) -> tuple[list[tuple[int, str, str, str]], dict[str, str]]:
    """Commits (offset, hash, time, subject) and header fields from history_text's output."""
    header: dict[str, str] = {}
    commits = []
    pos = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r"^([0-9a-f]{40}) (\S+) ?(.*)$", line.rstrip("\n"))
        if match:
            commits.append((pos, match.group(1), match.group(2), match.group(3)))
        elif ": " in line and not commits:
            key, _, value = line.partition(": ")
            header[key.strip().lower()] = value.strip()
        pos += len(line)
    return commits, header

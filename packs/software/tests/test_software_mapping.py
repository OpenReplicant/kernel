"""The software adapter without a database: the fixture regenerates from its sources, the
parsers read compose, Dockerfile, workflow and git conventions correctly, and a repository
is read the same at a commit and in its working tree."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from wmk_software import fixture
from wmk_software.mapping import (
    build,
    describe_on,
    from_lines,
    image_purl,
    interpolate,
    parse_action,
    parse_port,
    parse_volume,
    split_ref,
)
from wmk_software.plan import SELF, similarity
from wmk_software.repo import normalize_url, read, repo_name

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "kernel-stack"


def test_the_fixture_script_is_what_the_adapter_writes_today() -> None:
    """script.yaml is generated: regenerate it with `wmk-software snapshot` after changing the
    adapter, then check the eval still matches expected.yaml."""
    plan = fixture.plan_for(FIXTURE)
    assert fixture.script_text(plan) == (FIXTURE / "script.yaml").read_text()
    meta = yaml.safe_load((FIXTURE / "fixture.yaml").read_text())
    for source in plan.sources:
        args = source.ingest_args()
        del args["content"]
        assert {k: v for k, v in meta["sources"][source.alias].items() if k != "file"} == args


def test_every_claim_cites_its_file_and_names_existing_nodes() -> None:
    plan = fixture.plan_for(FIXTURE)
    aliases = {s.alias: s for s in plan.sources}
    for fact in plan.facts:
        if fact.basis == "observed":
            assert fact.source in aliases
            assert 0 <= fact.at < len(aliases[fact.source].content)
        for key in fact.node_keys():
            assert key in plan.nodes
    # Near-namesakes the kernel's trigram stage would refuse are declared distinct.
    steps = plan.script()
    creates = [op for s in steps if "write" in s for op in s["write"]["ops"] if op["op"] == "create"]
    later = next(op for op in creates if op["name"] == "wmk/otel-collector:4318")
    assert later["distinct_from"] == ["{{ref:ep_wmk_otel_collector_4317}}"]
    assert similarity("wmk/otel-collector:4317", "wmk/otel-collector:4318") > 0.8


def test_the_self_boundary_is_one_claim_per_part() -> None:
    plan = fixture.plan_for(FIXTURE)
    boundary = [f for f in plan.facts if any(op.get("to") == "system:self" for op in f.ops)]
    parts = {op["from"]: f.confidence for f in boundary for op in f.ops if op.get("edge") == "part_of"}
    assert parts[SELF] == "medium"
    assert parts["stack:wmk"] == "high" and parts["repo"] == "high"
    assert {parts[k] for k in parts if k.startswith("pipe:")} == {"low"}
    assert all(f.basis == "inferred" and f.source is None and f.once for f in boundary)


@pytest.mark.parametrize(
    ("entry", "port"),
    [
        ("127.0.0.1:${WMK_DB_PORT:-5432}:5432", (5432, "127.0.0.1:5432", "tcp", ["WMK_DB_PORT"])),
        ("8080:80", (80, "8080", "tcp", [])),
        ("53:53/udp", (53, "53", "udp", [])),
        ("3000", (3000, None, "tcp", [])),
        ("[::1]:8000:8000", (8000, "[::1]:8000", "tcp", [])),
        ({"target": 80, "published": "8080", "host_ip": "127.0.0.1"}, (80, "127.0.0.1:8080", "tcp", [])),
        ("${PORT}:80", (80, "${PORT}", "tcp", ["PORT"])),
    ],
)
def test_compose_ports(entry: Any, port: tuple[Any, ...]) -> None:
    assert parse_port(entry) == port


def test_compose_interpolation_and_volumes() -> None:
    assert interpolate("${A:-x}-${B-y}-${C}-$D") == ("x-y-${C}-$D", ["A", "B", "C", "D"])
    assert parse_volume("db-data:/var/lib/postgresql") == ("db-data", "/var/lib/postgresql", False)
    assert parse_volume("cache:/cache:ro") == ("cache", "/cache", True)
    assert parse_volume("./ui/site:/srv:ro") is None
    assert parse_volume({"type": "volume", "source": "logs", "target": "/logs"}) == ("logs", "/logs", False)
    assert parse_volume({"type": "bind", "source": "/etc", "target": "/etc"}) is None


@pytest.mark.parametrize(
    ("ref", "parts", "purl"),
    [
        ("postgres:18", ("postgres", "18", None), "pkg:docker/postgres"),
        ("caddy@sha256:abc", ("caddy", None, "sha256:abc"), "pkg:docker/caddy"),
        (
            "docker.io/library/python:3.12-slim",
            ("docker.io/library/python", "3.12-slim", None),
            "pkg:docker/python",
        ),
        ("localhost:5000/app:1.0", ("localhost:5000/app", "1.0", None), "pkg:docker/localhost:5000/app"),
        ("ghcr.io/o/r:v1@sha256:x", ("ghcr.io/o/r", "v1", "sha256:x"), "pkg:docker/ghcr.io/o/r"),
    ],
)
def test_image_references(ref: str, parts: tuple[Any, ...], purl: str) -> None:
    assert split_ref(ref) == parts
    assert image_purl(parts[0]) == purl


def test_dockerfile_from_lines() -> None:
    content = (
        "# syntax=docker/dockerfile:1\nARG BASE=python:3.12\n"
        "FROM --platform=linux/amd64 ${BASE} AS build\nRUN make \\\n  all\n"
        "FROM build\nFROM gcr.io/distroless/base AS run\n"
    )
    lines = from_lines(content)
    assert [(ref, stage) for _, ref, stage in lines] == [
        ("python:3.12", "build"),
        ("build", None),
        ("gcr.io/distroless/base", "run"),
    ]
    assert content[lines[0][0] :].startswith("FROM --platform")


def test_workflow_conventions() -> None:
    # YAML 1.1 reads `on:` as the boolean true; both spellings are handled by the mapper.
    assert describe_on({"push": {"branches": ["main"]}, "pull_request": None}) == [
        "push to main",
        "pull_request",
    ]
    assert describe_on(["push", "workflow_dispatch"]) == ["push", "workflow_dispatch"]
    assert parse_action("actions/checkout@v4") == ("actions/checkout", "v4")
    assert parse_action("github/codeql-action/init@v3") == ("github/codeql-action", "v3")
    assert parse_action("./.github/actions/local") is None
    assert parse_action("docker://alpine:3") is None


@pytest.mark.parametrize(
    ("remote", "url"),
    [
        ("git@github.com:Owner/Repo.git", "https://github.com/Owner/Repo"),
        ("https://user:token@github.com/o/r.git", "https://github.com/o/r"),
        ("ssh://git@host.example:2222/o/r.git", "https://host.example/o/r"),
        ("http://localhost:3000/o/r", "http://localhost:3000/o/r"),
    ],
)
def test_remote_urls(remote: str, url: str) -> None:
    assert normalize_url(remote) == url
    assert repo_name(url) == "/".join(url.split("/")[-2:])


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "toy"
    (root / "svc").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "toy"\nversion = "1.0"\ndependencies = ["requests>=2"]\n\n'
        '[tool.uv.workspace]\nmembers = ["svc"]\n'
    )
    (root / "svc" / "pyproject.toml").write_text('[project]\nname = "toy-svc"\nversion = "0.1"\n')
    (root / "compose.yaml").write_text(
        "services:\n  app:\n    build: {context: ., dockerfile: svc/Dockerfile}\n"
        '    ports: ["8000:8000"]\n    depends_on: [cache]\n  cache:\n    image: redis:7\n'
    )
    (root / "svc" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / ".github" / "workflows" / "test.yml").write_text(
        "name: test\non: [push]\njobs:\n  unit:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v4\n      - run: make test\n"
    )
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", "git@github.com:example/toy.git")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "First version (#1)")
    return root


def test_a_repository_at_a_commit_and_in_its_working_tree(toy_repo: Path) -> None:
    repo = read(toy_repo, rev="HEAD")
    assert repo.url == "https://github.com/example/toy" and repo.name == "example/toy"
    assert list(repo.files) == [
        "pyproject.toml",
        "svc/pyproject.toml",
        "compose.yaml",
        "svc/Dockerfile",
        ".github/workflows/test.yml",
    ]
    assert "First version (#1)" in repo.history
    # The working tree includes an untracked workflow; the commit does not.
    (toy_repo / ".github" / "workflows" / "lint.yml").write_text("name: lint\non: push\njobs: {}\n")
    assert ".github/workflows/lint.yml" in read(toy_repo).files
    assert ".github/workflows/lint.yml" not in read(toy_repo, rev="HEAD").files

    plan = build(repo, self_system="Toy")
    texts = [f.text for f in plan.facts]
    assert (
        "The toy stack has the service app, which runs the image toy-app, built from svc/Dockerfile." in texts
    )
    assert "toy/app needs toy/cache to have started before it starts." in texts
    assert "toy depends on requests >=2." in texts
    assert any(
        t.startswith("The test workflow's job unit checks example/toy on push; it runs make test")
        for t in texts
    )
    head = git(toy_repo, "rev-parse", "HEAD").strip()
    assert plan.nodes["commit:" + head[:12]]["props"] == {"commit": head, "pr": 1}

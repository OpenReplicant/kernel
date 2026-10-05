"""From a repository to a plan: what each file says, as observed claims citing it.

Every claim is about what a file states (a compose file defines a service, a pyproject
declares a dependency). Whether the running system matches is a separate observation.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
from typing import Any

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from wmk_software import manifests
from wmk_software.plan import SELF, Fact, Plan, Source
from wmk_software.repo import COMPOSE_FILES, WORKFLOWS, Repository, build_spec, file_uri, parse_history

NS = "software"
CONDITIONS = {
    "service_started": "to have started",
    "service_healthy": "to be healthy",
    "service_completed_successfully": "to have finished successfully",
}


def build(repo: Repository, *, self_system: str | None = None) -> Plan:
    """The plan for a repository. With `self_system`, also the self boundary: the system of
    that name, with the writing agent, the stack, the repository and its pipelines as parts."""
    mapper = Mapper(repo)
    mapper.history()
    for path in repo.files:
        if path == "pyproject.toml" or path.endswith("/pyproject.toml"):
            mapper.pyproject(path)
    for path in repo.files:
        if path in COMPOSE_FILES:
            mapper.compose(path)
    for path, images in mapper.built.items():
        if path in repo.files:
            mapper.dockerfile(path, images)
    for path in repo.files:
        if path.startswith(WORKFLOWS):
            mapper.workflow(path)
    kinds = {path: manifests.manifest_type(path, text) for path, text in repo.files.items()}
    manifests.kubernetes(mapper, [p for p, k in kinds.items() if k == "kubernetes"])
    for path in [p for p, k in kinds.items() if k == "openapi"]:
        manifests.openapi(mapper, path)
    if self_system:
        mapper.self_boundary(self_system)
    mapper.plan.drop_empty()
    return mapper.plan


class Mapper:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.plan = Plan()
        self.built: dict[str, list[tuple[str, str]]] = {}  # Dockerfile -> [(image key, name)]
        self.stack: str | None = None
        self.pipelines: list[tuple[str, str]] = []

    # Sources ----------------------------------------------------------------------------

    def file_source(self, path: str) -> Source:
        r = self.repo
        return self.plan.source(
            Source(
                alias=path,
                content=r.files[path],
                title=f"{path} in {r.name}",
                uri=file_uri(r.url, path),
                collection=f"{r.url}#{path}",
                metadata={"repository": r.url, "path": path, "commit": r.commit},
                origins=(f"repo:{r.url}",),
            )
        )

    def node(self, key: str, kind: str, name: str, **extra: Any) -> str:
        return self.plan.node(key, type="Entity", kind=kind, namespace=NS, name=name, **extra)

    # Git history ------------------------------------------------------------------------

    def history(self) -> None:
        r = self.repo
        src = self.plan.source(
            Source(
                alias="git-history",
                content=r.history,
                title=f"Git history of {r.name}",
                uri=r.url,
                collection=f"{r.url}#git-history",
                metadata={"repository": r.url, "branch": r.branch, "commit": r.commit},
                append_only=True,
                origins=(f"repo:{r.url}",),
            )
        )
        repo = self.node("repo", "repository", r.name, identity={"url": r.url})
        fact = self.plan.fact(f"{r.name} is a git repository at {r.url}.", source=src.alias, once=True)
        self.plan.mention(fact, repo)
        commits, _ = parse_history(r.history)
        for offset, sha, when, subject in reversed(commits):
            pr = re.search(r"\(#(\d+)\)\s*$", subject)
            key = self.plan.node(
                f"commit:{sha[:12]}",
                type="Event",
                kind="change",
                namespace=NS,
                name=subject or sha[:7],
                identity={"commit": sha},
                start=when,
                **({"props": {"pr": int(pr.group(1))}} if pr else {}),
            )
            fact = self.plan.fact(
                f"Commit {sha[:7]} on {when[:10]} changed {r.name}: {subject}".rstrip(": ") + ".",
                source=src.alias,
                at=offset,
                once=True,
            )
            self.plan.edge(fact, "participates_in", repo, key, {"role": "changed"})

    # pyproject.toml ---------------------------------------------------------------------

    def pyproject(self, path: str) -> None:
        src = self.file_source(path)
        data = tomllib.loads(src.content)
        project = data.get("project") or {}
        name = project.get("name")
        if not name:
            return
        version = project.get("version")
        pkg = self.package(name)
        fact = self.plan.fact(
            f"{path} defines the Python package {name}{f' {version}' if version else ''}.",
            source=path,
            at=find(src.content, f'name = "{name}"'),
        )
        self.plan.edge(
            fact, "defines", "repo", pkg, {"path": path, **({"version": version} if version else {})}
        )
        groups: list[tuple[str, list[Any]]] = [("main", project.get("dependencies") or [])]
        groups += [(f"extra {k}", v) for k, v in (project.get("optional-dependencies") or {}).items()]
        groups += list((data.get("dependency-groups") or {}).items())
        for group, requirements in groups:
            for text in requirements:
                if isinstance(text, str):
                    self.requirement(src, name, pkg, text, group)

    def package(self, name: str) -> str:
        canon = canonicalize_name(name)
        purl = f"pkg:pypi/{canon}"
        return self.node(purl, "package", canon, identity={"purl": purl})

    def requirement(self, src: Source, owner: str, pkg: str, text: str, group: str) -> None:
        try:
            req = Requirement(text)
        except InvalidRequirement:
            return
        dep = self.package(req.name)
        spec = specifiers(req)
        extras = sorted(req.extras)
        props: dict[str, Any] = {"group": group}
        if spec:
            props["constraint"] = spec
        if extras:
            props["extras"] = extras
        if req.marker:
            props["marker"] = str(req.marker)
        said = f"{req.name}{'[' + ','.join(extras) + ']' if extras else ''}{f' {spec}' if spec else ''}"
        where = "" if group == "main" else f" in its {group} group"
        when = f" when {req.marker}" if req.marker else ""
        fact = self.plan.fact(
            f"{owner} depends on {said}{where}{when}.", source=src.alias, at=find(src.content, text)
        )
        self.plan.edge(fact, "uses_package", pkg, dep, props)

    # Compose ----------------------------------------------------------------------------

    def compose(self, path: str) -> None:
        src = self.file_source(path)
        data = yaml.safe_load(src.content) or {}
        tree = yaml.compose(src.content)
        r = self.repo
        project = str(data.get("name") or r.name.split("/")[-1])
        self.stack = stack = self.node(
            f"stack:{project}", "stack", project, identity={"defined_at": f"{r.url}#{project}"}
        )
        fact = self.plan.fact(
            f"{path} defines the Compose stack {project}.", source=path, at=located(tree, "name")
        )
        self.plan.edge(fact, "defines", "repo", stack, {"path": path})
        services: dict[str, Any] = data.get("services") or {}
        keys = {s: self.service(project, s) for s in services}
        for s, spec in services.items():
            self.service_fact(src, tree, project, s, spec or {}, keys[s], stack)
        for s, spec in services.items():
            self.depends(src, tree, project, s, spec or {}, keys)
            self.ports(src, tree, project, s, spec or {}, keys[s])
            self.volumes(src, tree, project, s, spec or {}, keys[s], data.get("volumes") or {})
            self.networks(src, tree, project, s, spec or {}, keys[s], data.get("networks") or {})

    def service(self, project: str, name: str) -> str:
        full = f"{project}/{name}"
        return self.node(f"svc:{full}", "service", full, identity={"defined_at": f"{self.repo.url}#{full}"})

    def service_fact(
        self, src: Source, tree: Any, project: str, s: str, spec: dict[str, Any], svc: str, stack: str
    ) -> None:
        profiles = [str(p) for p in spec.get("profiles") or []]
        build = build_spec(spec)
        image = str(spec.get("image") or "")
        runs: dict[str, Any] = {}
        if build:
            _, dockerfile = build
            name, tag, _ = split_ref(image) if image else (f"{project}-{s}", None, None)
            img = self.node(f"img:{name}", "image", name)
            self.built.setdefault(dockerfile, [])
            if (img, name) not in self.built[dockerfile]:
                self.built[dockerfile].append((img, name))
            what = f"the image {image or name}, built from {dockerfile}"
            if tag:
                runs["tag"] = tag
        elif image:
            name, tag, digest = split_ref(image)
            img = self.image(name)
            what = f"the image {image}"
            runs = {k: v for k, v in (("tag", tag), ("digest", digest)) if v}
        else:
            img, what = None, "no image"
        only = f"; it starts only with the {' and '.join(profiles)} profile" if profiles else ""
        fact = self.plan.fact(
            f"The {project} stack has the service {s}, which runs {what}{only}.",
            source=src.alias,
            at=located(tree, "services", s),
        )
        self.plan.edge(fact, "part_of", svc, stack, {"profiles": profiles} if profiles else None)
        if img:
            self.plan.edge(fact, "runs", svc, img, runs or None)
            if build:
                self.plan.edge(fact, "built_from", img, "repo", {"dockerfile": build[1], "context": build[0]})

    def image(self, name: str) -> str:
        purl = image_purl(name)
        return self.node(f"img:{name}", "image", name, identity={"purl": purl})

    def depends(
        self, src: Source, tree: Any, project: str, s: str, spec: dict[str, Any], keys: dict[str, str]
    ) -> None:
        deps = spec.get("depends_on") or {}
        if isinstance(deps, list):
            deps = {d: {} for d in deps}
        for dep, how in deps.items():
            condition = str((how or {}).get("condition", "service_started"))
            target = keys.get(dep) or self.service(project, dep)
            fact = self.plan.fact(
                f"{project}/{s} needs {project}/{dep} {CONDITIONS.get(condition, f'({condition})')} "
                "before it starts.",
                source=src.alias,
                at=located(tree, "services", s, "depends_on", dep),
            )
            self.plan.edge(fact, "needs", keys[s], target, {"condition": condition})

    def ports(self, src: Source, tree: Any, project: str, s: str, spec: dict[str, Any], svc: str) -> None:
        for i, entry in enumerate(spec.get("ports") or []):
            port = parse_port(entry)
            if port is None:
                continue
            target, published, protocol, variables = port
            full = f"{project}/{s}:{target}"
            ep = self.node(f"ep:{full}", "endpoint", full, identity={"defined_at": f"{self.repo.url}#{full}"})
            props: dict[str, Any] = {"target": target}
            if published:
                props["published"] = published
            if protocol != "tcp":
                props["protocol"] = protocol
            if variables:
                props["variables"] = variables
            said = (
                f"publishes container port {target} on {published}" if published else f"exposes port {target}"
            )
            set_by = f" (set by {', '.join(variables)})" if variables else ""
            fact = self.plan.fact(
                f"{project}/{s} {said}{set_by}.",
                source=src.alias,
                at=located(tree, "services", s, "ports", i),
            )
            self.plan.edge(fact, "exposed_by", ep, svc, props)

    def volumes(
        self,
        src: Source,
        tree: Any,
        project: str,
        s: str,
        spec: dict[str, Any],
        svc: str,
        declared: dict[str, Any],
    ) -> None:
        for i, entry in enumerate(spec.get("volumes") or []):
            mount = parse_volume(entry)
            if mount is None:
                continue
            volume, target, read_only = mount
            real = str((declared.get(volume) or {}).get("name") or f"{project}_{volume}")
            vol = self.node(
                f"vol:{project}/{volume}",
                "volume",
                real,
                identity={"defined_at": f"{self.repo.url}#{project}/volumes/{volume}"},
            )
            props: dict[str, Any] = {"target": target}
            if read_only:
                props["read_only"] = True
            fact = self.plan.fact(
                f"{project}/{s} mounts the volume {real} at {target}{' read-only' if read_only else ''}.",
                source=src.alias,
                at=located(tree, "services", s, "volumes", i),
            )
            self.plan.edge(fact, "mounts", svc, vol, props)

    def networks(
        self,
        src: Source,
        tree: Any,
        project: str,
        s: str,
        spec: dict[str, Any],
        svc: str,
        declared: dict[str, Any],
    ) -> None:
        networks = spec.get("networks") or []
        names = list(networks) if isinstance(networks, dict) else [str(n) for n in networks]
        for net in names:
            real = str((declared.get(net) or {}).get("name") or f"{project}_{net}")
            node = self.node(
                f"net:{project}/{net}",
                "network",
                real,
                identity={"defined_at": f"{self.repo.url}#{project}/networks/{net}"},
            )
            fact = self.plan.fact(
                f"{project}/{s} is attached to the network {real}.",
                source=src.alias,
                at=located(
                    tree, "services", s, "networks", net if isinstance(networks, dict) else names.index(net)
                ),
            )
            self.plan.edge(fact, "attached_to", svc, node)

    # Dockerfiles ------------------------------------------------------------------------

    def dockerfile(self, path: str, images: list[tuple[str, str]]) -> None:
        src = self.file_source(path)
        stages: set[str] = set()
        for offset, ref, stage in from_lines(src.content):
            if ref.lower() in stages or ref == "scratch":
                if stage:
                    stages.add(stage.lower())
                continue
            name, tag, digest = split_ref(ref)
            base = self.image(name)
            props = {k: v for k, v in (("tag", tag), ("digest", digest), ("stage", stage)) if v}
            for img, built in images:
                in_stage = f" in its {stage} stage" if stage else ""
                fact = self.plan.fact(
                    f"{path} builds {built} on the base image {ref}{in_stage}.", source=path, at=offset
                )
                self.plan.edge(fact, "based_on", img, base, props or None)
            if stage:
                stages.add(stage.lower())

    # CI workflows -----------------------------------------------------------------------

    def workflow(self, path: str) -> None:
        src = self.file_source(path)
        data = yaml.safe_load(src.content) or {}
        tree = yaml.compose(src.content)
        name = str(data.get("name") or posixpath.splitext(posixpath.basename(path))[0])
        # YAML 1.1 reads the key `on` as true.
        events = describe_on(data.get("on", data.get(True)))
        for job_id, job in (data.get("jobs") or {}).items():
            job = job or {}
            steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
            runs = [
                line.strip() + (f" (if {s['if']})" if s.get("if") else "")
                for s in steps
                for line in str(s.get("run", "")).splitlines()
                if line.strip()
            ]
            uses = [str(s["uses"]) for s in steps if "uses" in s]
            full = f"{name}/{job_id}"
            pipe = self.node(
                f"pipe:{path}/{job_id}",
                "pipeline",
                full,
                identity={"defined_at": f"{self.repo.url}#{path}/{job_id}"},
            )
            self.pipelines.append((pipe, full))
            ran = f"; it runs {'; '.join(runs)}" if runs else ""
            used = f"; it uses {', '.join(uses)}" if uses else ""
            fact = self.plan.fact(
                f"The {name} workflow's job {job_id} checks {self.repo.name} on "
                f"{' and '.join(events) or 'demand'}{ran}{used}.",
                source=path,
                at=located(tree, "jobs", job_id),
            )
            self.plan.edge(fact, "defines", "repo", pipe, {"path": path})
            self.plan.edge(fact, "checks", pipe, "repo", {"on": events} if events else None)
            for action in uses:
                parsed = parse_action(action)
                if parsed is None:
                    continue
                slug, version = parsed
                purl = f"pkg:github/{slug.lower()}"
                pkg = self.node(purl, "package", slug, identity={"purl": purl})
                self.plan.edge(fact, "uses_package", pipe, pkg, {"version": version} if version else None)

    # The self boundary ------------------------------------------------------------------

    def self_boundary(self, system: str) -> None:
        r = self.repo
        key = self.plan.node(
            "system:self",
            type="Entity",
            kind="system",
            namespace=NS,
            name=system,
            identity={"instance": f"{r.url}#self"},
        )
        fact = self.inferred(
            f"{system} is the system this kernel instance runs as, and the agent writing these claims "
            "is part of it.",
            "medium",
        )
        self.plan.mention(fact, key)
        self.plan.edge(fact, "part_of", SELF, key)
        if self.stack:
            stack_name = self.plan.nodes[self.stack]["name"]
            fact = self.inferred(
                f"The {stack_name} stack is part of {system}: it runs the system's services."
            )
            self.plan.edge(fact, "part_of", self.stack, key)
        fact = self.inferred(
            f"The repository {r.name} is part of {system}: it holds the system's code and configuration."
        )
        self.plan.edge(fact, "part_of", "repo", key)
        for pipe, name in self.pipelines:
            fact = self.inferred(
                f"The pipeline {name} is part of {system}: it checks the system's code on every change, "
                "though it runs outside the stack.",
                "low",
            )
            self.plan.edge(fact, "part_of", pipe, key)

    def inferred(self, text: str, confidence: str = "high") -> Fact:
        return self.plan.fact(text, basis="inferred", confidence=confidence, once=True)


# Parsing helpers ----------------------------------------------------------------------------


def find(content: str, needle: str) -> int:
    """Offset of `needle` in `content`, or 0 when it is not there verbatim."""
    at = content.find(needle)
    return max(at, 0)


def located(tree: Any, *path: Any) -> int:
    """Character offset of the deepest key or item on `path` in a composed YAML document."""
    node, at = tree, 0
    for step in path:
        if isinstance(node, yaml.MappingNode):
            match = next(((k, v) for k, v in node.value if k.value == str(step)), None)
            if match is None:
                break
            at, node = match[0].start_mark.index, match[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(step, int) and step < len(node.value):
            node = node.value[step]
            at = node.start_mark.index
        else:
            break
    return at


def specifiers(req: Requirement) -> str:
    """The version range, lower bounds first: >=3.3,<4."""
    order = {">=": 0, ">": 0, "==": 1, "~=": 1, "===": 1, "!=": 2, "<=": 3, "<": 3}
    return ",".join(str(s) for s in sorted(req.specifier, key=lambda s: (order.get(s.operator, 4), str(s))))


def split_ref(ref: str) -> tuple[str, str | None, str | None]:
    """(name, tag, digest) of an image reference such as postgres:18 or caddy@sha256:..."""
    name, _, digest = ref.partition("@")
    tag = None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    return name, tag, digest or None


def image_purl(name: str) -> str:
    """pkg:docker/<namespace>/<name> without the default registry or library/ prefix."""
    name = name.lower()
    for prefix in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    name = name.removeprefix("library/")
    return f"pkg:docker/{name}"


_VAR = re.compile(r"\$\{(\w+)(?::?-([^}]*))?\}|\$(\w+)")


def interpolate(text: str) -> tuple[str, list[str]]:
    """Compose variable interpolation with defaults: ${PORT:-8000} becomes 8000."""
    names: list[str] = []

    def sub(m: re.Match[str]) -> str:
        names.append(m.group(1) or m.group(3))
        return m.group(2) if m.group(2) is not None else m.group(0)

    return _VAR.sub(sub, text), names


def parse_port(entry: Any) -> tuple[int, str | None, str, list[str]] | None:
    """(container port, published host address, protocol, variables) of a compose port."""
    if isinstance(entry, dict):
        target = entry.get("target")
        published = entry.get("published")
        host = entry.get("host_ip")
        text = f"{host}:{published}" if host and published else (str(published) if published else None)
        value, names = interpolate(text) if text else (None, [])
        return (int(target), value, str(entry.get("protocol", "tcp")), names) if target else None
    value, names = interpolate(str(entry))
    spec, _, protocol = value.partition("/")
    parts = spec.rsplit(":", 2) if spec.count(":") >= 2 else spec.split(":")
    try:
        target = int(parts[-1].split("-")[0])
    except ValueError:
        return None
    published = ":".join(parts[:-1]) or None
    return target, published, protocol or "tcp", names


def parse_volume(entry: Any) -> tuple[str, str, bool] | None:
    """(named volume, target, read-only) for a named-volume mount; None for bind mounts."""
    if isinstance(entry, dict):
        if entry.get("type", "volume") != "volume" or not entry.get("source"):
            return None
        return str(entry["source"]), str(entry.get("target", "")), bool(entry.get("read_only"))
    parts = str(entry).split(":")
    if len(parts) < 2 or parts[0].startswith((".", "/", "~", "$")):
        return None
    return parts[0], parts[1], len(parts) > 2 and "ro" in parts[2].split(",")


def from_lines(content: str) -> list[tuple[int, str, str | None]]:
    """(offset, image reference, stage name) for each FROM line, with ARG defaults applied."""
    args: dict[str, str] = {}
    out = []
    pos = 0
    logical = ""
    start = 0
    for line in content.splitlines(keepends=True):
        if not logical:
            start = pos
        pos += len(line)
        stripped = line.rstrip("\n").rstrip()
        if stripped.endswith("\\"):
            logical += stripped[:-1] + " "
            continue
        logical += stripped
        text, logical = logical.strip(), ""
        if not text or text.startswith("#"):
            continue
        arg = re.match(r"(?i)^ARG\s+(\w+)(?:=(\S*))?", text)
        if arg and arg.group(2) is not None and not out:
            args[arg.group(1)] = arg.group(2).strip("\"'")
            continue
        match = re.match(r"(?i)^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$", text)
        if match:
            ref = re.sub(r"\$\{?(\w+)\}?", lambda m: args.get(m.group(1), m.group(0)), match.group(1))
            out.append((start, ref, match.group(2)))
    return out


def describe_on(on: Any) -> list[str]:
    """The events a workflow runs on: push to main, pull_request, schedule, ..."""
    if on is None:
        return []
    if isinstance(on, str):
        return [on]
    if isinstance(on, list):
        return [str(e) for e in on]
    out = []
    for event, spec in on.items():
        branches = (spec or {}).get("branches") if isinstance(spec, dict) else None
        out.append(f"{event} to {', '.join(map(str, branches))}" if branches else str(event))
    return out


def parse_action(uses: str) -> tuple[str, str | None] | None:
    """(owner/repo, ref) for a GitHub Action; None for local and docker:// actions."""
    if uses.startswith(("./", "docker://")):
        return None
    action, _, ref = uses.partition("@")
    parts = action.split("/")
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}", ref or None

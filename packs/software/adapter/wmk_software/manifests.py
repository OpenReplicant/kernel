"""Kubernetes manifests and OpenAPI documents, mapped like the compose file: what each file
declares, as observed claims citing it.

Kubernetes: a namespace is a stack; a Deployment, StatefulSet, DaemonSet, Job or CronJob is
a service (`<namespace>/<name>`) that runs the images of its containers and mounts its
volume claims; a Service's ports are endpoints of the workloads its selector matches, and
an Ingress rule is an endpoint of the workloads behind its backend Service.

OpenAPI: each operation is an endpoint (`<API title> <METHOD> <path>`) of the service that
serves the API: the compose or Kubernetes service named by the document's `x-service`
extension (`<stack>/<service>`), else a service of the API's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from wmk_software.plan import Source

if TYPE_CHECKING:
    from wmk_software.mapping import Mapper

WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
KUBERNETES_KINDS = (*WORKLOADS, "Namespace", "Service", "PersistentVolumeClaim", "Ingress")
METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def manifest_type(path: str, text: str) -> str | None:
    """'kubernetes' or 'openapi' for a YAML or JSON file that is one, else None."""
    if not path.endswith((".yaml", ".yml", ".json")) or len(text) > 2_000_000:
        return None
    try:
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError:
        return None
    if any(d.get("apiVersion") and d.get("kind") in KUBERNETES_KINDS for d in docs):
        return "kubernetes"
    if len(docs) == 1 and "paths" in docs[0]:
        doc = docs[0]
        if str(doc.get("openapi", "")).startswith("3") or str(doc.get("swagger", "")) == "2.0":
            return "openapi"
    return None


@dataclass
class Workload:
    key: str
    name: str  # <namespace>/<name>
    namespace: str
    labels: dict[str, str]


@dataclass
class Obj:
    """One Kubernetes object, its composed YAML node (for offsets) and the file it is in."""

    doc: dict[str, Any]
    tree: Any
    src: Source

    @property
    def kind(self) -> str:
        return str(self.doc.get("kind"))

    @property
    def name(self) -> str:
        return str((self.doc.get("metadata") or {}).get("name"))

    @property
    def namespace(self) -> str:
        return str((self.doc.get("metadata") or {}).get("namespace") or "default")


def kubernetes(m: Mapper, paths: list[str]) -> None:
    from wmk_software.mapping import located

    objects: list[Obj] = []
    for path in paths:
        src = m.file_source(path)
        trees = list(yaml.compose_all(src.content))
        for doc, tree in zip(yaml.safe_load_all(src.content), trees, strict=True):
            if (
                isinstance(doc, dict)
                and doc.get("kind") in KUBERNETES_KINDS
                and (doc.get("metadata") or {}).get("name")
            ):
                objects.append(Obj(doc, tree, src))
    workloads = [workload(m, o) for o in objects if o.kind in WORKLOADS]
    routes: dict[tuple[str, str], list[Workload]] = {}
    for o in objects:
        if o.kind != "Service":
            continue
        selector = (o.doc.get("spec") or {}).get("selector") or {}
        matched = [
            w
            for w in workloads
            if w.namespace == o.namespace
            and selector
            and all(w.labels.get(k) == v for k, v in selector.items())
        ]
        routes[(o.namespace, o.name)] = matched
        for i, port in enumerate((o.doc.get("spec") or {}).get("ports") or []):
            number = port.get("port")
            target = port.get("targetPort", number)
            full = f"{o.name}.{o.namespace}:{number}"
            ep = m.node(
                f"ep:k8s:{full}", "endpoint", full, identity={"defined_at": f"{m.repo.url}#k8s:{full}"}
            )
            props: dict[str, Any] = {"target": target, "port": number}
            if str(port.get("protocol", "TCP")) != "TCP":
                props["protocol"] = port["protocol"]
            for w in matched:
                fact = m.plan.fact(
                    f"The Kubernetes Service {o.namespace}/{o.name} routes port {number} "
                    f"to port {target} of {w.name}.",
                    source=o.src.alias,
                    at=located(o.tree, "spec", "ports", i),
                )
                m.plan.edge(fact, "exposed_by", ep, w.key, props)
    for o in objects:
        if o.kind == "Ingress":
            ingress(m, o, routes)


def namespace_stack(m: Mapper, o: Obj) -> str:
    from wmk_software.mapping import located

    ns = o.namespace
    stack = m.node(f"stack:k8s:{ns}", "stack", ns, identity={"defined_at": f"{m.repo.url}#k8s:{ns}"})
    fact = m.plan.fact(
        f"{o.src.alias} defines Kubernetes objects in the namespace {ns}.",
        source=o.src.alias,
        at=located(o.tree, "metadata"),
    )
    m.plan.edge(fact, "defines", "repo", stack, {"path": o.src.alias})
    return stack


def workload(m: Mapper, o: Obj) -> Workload:
    from wmk_software.mapping import located, split_ref

    full = f"{o.namespace}/{o.name}"
    stack = namespace_stack(m, o)
    key = m.node(f"svc:k8s:{full}", "service", full, identity={"defined_at": f"{m.repo.url}#k8s:{full}"})
    spec = o.doc.get("spec") or {}
    at_pod = ("spec", "jobTemplate", "spec", "template") if o.kind == "CronJob" else ("spec", "template")
    template: dict[str, Any] = o.doc
    for step in at_pod:
        template = (template or {}).get(step) or {}
    pod = template.get("spec") or {}
    labels = {str(k): str(v) for k, v in ((template.get("metadata") or {}).get("labels") or {}).items()}
    fact = m.plan.fact(
        f"The Kubernetes {o.kind} {full} runs in the namespace {o.namespace}.",
        source=o.src.alias,
        at=located(o.tree, "metadata", "name"),
    )
    m.plan.edge(fact, "part_of", key, stack, {"kind": o.kind})
    claims = {
        str(v["name"]): str(v["persistentVolumeClaim"]["claimName"])
        for v in pod.get("volumes") or []
        if isinstance(v, dict) and isinstance(v.get("persistentVolumeClaim"), dict)
    }
    for t in spec.get("volumeClaimTemplates") or []:
        name = str((t.get("metadata") or {}).get("name"))
        claims[name] = name
    for group in ("initContainers", "containers"):
        for i, c in enumerate(pod.get(group) or []):
            where = (*at_pod, "spec", group, i)
            container = str(c.get("name"))
            init = group == "initContainers"
            if c.get("image"):
                image = str(c["image"])
                name, tag, digest = split_ref(image)
                props: dict[str, Any] = {"container": container}
                props |= {k: v for k, v in (("tag", tag), ("digest", digest)) if v}
                if init:
                    props["init"] = True
                fact = m.plan.fact(
                    f"{full} runs the image {image} in its {'init ' if init else ''}container {container}.",
                    source=o.src.alias,
                    at=located(o.tree, *where, "image"),
                )
                m.plan.edge(fact, "runs", key, m.image(name), props)
            for j, mount in enumerate(c.get("volumeMounts") or []):
                claim = claims.get(str(mount.get("name")))
                if not claim:
                    continue
                vol_name = f"{o.namespace}/{claim}"
                vol = m.node(
                    f"vol:k8s:{vol_name}",
                    "volume",
                    vol_name,
                    identity={"defined_at": f"{m.repo.url}#k8s:{vol_name}"},
                )
                target = str(mount.get("mountPath"))
                read_only = bool(mount.get("readOnly"))
                fact = m.plan.fact(
                    f"{full} mounts the volume claim {vol_name} at {target}"
                    f"{' read-only' if read_only else ''}.",
                    source=o.src.alias,
                    at=located(o.tree, *where, "volumeMounts", j),
                )
                m.plan.edge(
                    fact, "mounts", key, vol, {"target": target, **({"read_only": True} if read_only else {})}
                )
    return Workload(key, full, o.namespace, labels)


def ingress(m: Mapper, o: Obj, routes: dict[tuple[str, str], list[Workload]]) -> None:
    from wmk_software.mapping import located

    for i, rule in enumerate((o.doc.get("spec") or {}).get("rules") or []):
        host = str(rule.get("host") or "*")
        for j, entry in enumerate((rule.get("http") or {}).get("paths") or []):
            backend = (entry.get("backend") or {}).get("service") or {}
            service = str(backend.get("name"))
            port = (backend.get("port") or {}).get("number") or (backend.get("port") or {}).get("name")
            path = str(entry.get("path") or "/")
            full = f"{host}{path}"
            ep = m.node(
                f"ep:k8s-ingress:{full}",
                "endpoint",
                full,
                identity={"defined_at": f"{m.repo.url}#k8s:ingress:{full}"},
            )
            for w in routes.get((o.namespace, service), []):
                fact = m.plan.fact(
                    f"The Kubernetes Ingress {o.namespace}/{o.name} routes {full} to the Service {service} "
                    f"and so to {w.name}.",
                    source=o.src.alias,
                    at=located(o.tree, "spec", "rules", i, "http", "paths", j),
                )
                props = {"ingress": o.name, "service": service, **({"port": port} if port else {})}
                m.plan.edge(fact, "exposed_by", ep, w.key, props)


def openapi(m: Mapper, path: str) -> None:
    from wmk_software.mapping import located

    src = m.file_source(path)
    doc = yaml.safe_load(src.content) or {}
    tree = yaml.compose(src.content)
    info = doc.get("info") or {}
    title = str(info.get("title") or path)
    version = f" {info['version']}" if info.get("version") else ""
    served_by = str(doc.get("x-service") or "")
    if served_by and f"svc:k8s:{served_by}" in m.plan.nodes:
        svc = f"svc:k8s:{served_by}"
    elif served_by:
        svc = m.service(*served_by.split("/", 1)) if "/" in served_by else m.service(served_by, served_by)
    else:
        svc = m.node(f"svc:api:{title}", "service", title, identity={"defined_at": f"{m.repo.url}#{path}"})
    fact = m.plan.fact(f"{path} describes the {title} API{version}.", source=path, at=located(tree, "info"))
    m.plan.mention(fact, svc)
    for p, item in (doc.get("paths") or {}).items():
        for method in METHODS:
            op = (item or {}).get(method)
            if not isinstance(op, dict):
                continue
            verb = method.upper()
            full = f"{title} {verb} {p}"
            ep = m.node(
                f"ep:api:{title}:{verb} {p}",
                "endpoint",
                full,
                identity={"defined_at": f"{m.repo.url}#{path}:{verb} {p}"},
            )
            summary = str(op.get("summary") or "").strip().rstrip(".")
            fact = m.plan.fact(
                f"The {title} API serves {verb} {p}{f': {summary}' if summary else ''}.",
                source=path,
                at=located(tree, "paths", p, method),
            )
            props = {
                "method": verb,
                "path": str(p),
                **({"operation": op["operationId"]} if op.get("operationId") else {}),
            }
            m.plan.edge(fact, "exposed_by", ep, svc, props)

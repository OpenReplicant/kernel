"""Kubernetes manifests and OpenAPI documents: what they declare becomes services, images,
volumes and endpoints, each fact citing its file, and the kernel accepts the mapping."""

from __future__ import annotations

from typing import Any

import pytest

from wmk_software.apply import apply
from wmk_software.manifests import manifest_type
from wmk_software.mapping import build
from wmk_software.repo import Repository

URL = "https://github.com/example/shop"

APP = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: api, namespace: shop}
spec:
  template:
    metadata: {labels: {app: api, tier: web}}
    spec:
      initContainers:
        - {name: migrate, image: "ghcr.io/example/api:1.4"}
      containers:
        - name: api
          image: "ghcr.io/example/api:1.4"
          volumeMounts: [{name: uploads, mountPath: /data/uploads}]
      volumes:
        - {name: uploads, persistentVolumeClaim: {claimName: uploads}}
---
apiVersion: v1
kind: Service
metadata: {name: api, namespace: shop}
spec:
  selector: {app: api}
  ports: [{port: 80, targetPort: 8080}]
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: public, namespace: shop}
spec:
  rules:
    - host: shop.example.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend: {service: {name: api, port: {number: 80}}}
"""
DB = """\
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: db, namespace: shop}
spec:
  template:
    metadata: {labels: {app: db}}
    spec:
      containers:
        - name: postgres
          image: "postgres:18@sha256:abc"
          volumeMounts: [{name: data, mountPath: /var/lib/postgresql}]
  volumeClaimTemplates:
    - metadata: {name: data}
---
apiVersion: batch/v1
kind: CronJob
metadata: {name: backup, namespace: shop}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers: [{name: dump, image: "postgres:18"}]
"""
API = """\
openapi: 3.1.0
info: {title: Shop API, version: "1.4"}
x-service: shop/api
paths:
  /orders:
    get: {operationId: listOrders, summary: List orders.}
    post: {operationId: createOrder}
  /orders/{id}:
    get: {summary: One order}
"""


def repo() -> Repository:
    history = f"Repository: {URL}\nBranch: main\n\nFirst-parent commits, newest first:\n\n"
    history += f"{'a' * 40} 2026-10-01T12:00:00Z Deploy the shop (#1)\n"
    files = {"deploy/app.yaml": APP, "deploy/db.yaml": DB, "api/openapi.yaml": API, "README.yaml": "a: 1\n"}
    return Repository(
        url=URL, name="example/shop", commit="a" * 40, branch="main", files=files, history=history
    )


def edges(plan: Any) -> set[tuple[str, str, str]]:
    names = {k: v["name"] for k, v in plan.nodes.items()}
    names["repo"] = "repo"
    return {
        (op["edge"], names[op["from"]], names[op["to"]])
        for f in plan.facts
        for op in f.ops
        if op["op"] == "assert"
    }


def test_manifests_are_recognised_by_content() -> None:
    assert manifest_type("deploy/app.yaml", APP) == "kubernetes"
    assert manifest_type("api/openapi.yaml", API) == "openapi"
    assert manifest_type("README.yaml", "a: 1\n") is None
    assert manifest_type("chart/templates/x.yaml", "kind: {{ .Values.kind }}\n:") is None
    assert manifest_type("notes.txt", APP) is None


def test_kubernetes_and_openapi_map_to_services_images_volumes_and_endpoints() -> None:
    plan = build(repo())
    found = edges(plan)
    assert {
        ("defines", "repo", "shop"),
        ("part_of", "shop/api", "shop"),
        ("part_of", "shop/db", "shop"),
        ("part_of", "shop/backup", "shop"),
        ("runs", "shop/api", "ghcr.io/example/api"),
        ("runs", "shop/db", "postgres"),
        ("runs", "shop/backup", "postgres"),
        ("mounts", "shop/api", "shop/uploads"),
        ("mounts", "shop/db", "shop/data"),
        ("exposed_by", "api.shop:80", "shop/api"),
        ("exposed_by", "shop.example.com/api", "shop/api"),
        ("exposed_by", "Shop API GET /orders", "shop/api"),
        ("exposed_by", "Shop API POST /orders", "shop/api"),
        ("exposed_by", "Shop API GET /orders/{id}", "shop/api"),
    } <= found
    runs = {
        tuple(sorted(op.get("props", {}).items()))
        for f in plan.facts
        for op in f.ops
        if op["op"] == "assert" and op["edge"] == "runs"
    }
    assert (("container", "migrate"), ("init", True), ("tag", "1.4")) in runs
    assert (("container", "postgres"), ("digest", "sha256:abc"), ("tag", "18")) in runs
    # Every fact cites the file that states it, at the object it describes.
    sources = {s.alias: s for s in plan.sources}
    for fact in plan.facts:
        if fact.source in ("deploy/app.yaml", "deploy/db.yaml", "api/openapi.yaml"):
            assert 0 <= fact.at < len(sources[fact.source].content)
    assert "README.yaml" not in sources


@pytest.mark.anyio
async def test_the_kernel_accepts_the_mapping(gateway: Any) -> None:
    plan = build(repo())
    result = await apply(gateway, plan)
    assert result.failures == [], result.failures
    again = await apply(gateway, plan)
    assert (again.claims, again.failures) == (0, [])

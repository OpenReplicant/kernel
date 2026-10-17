"""The few Docker Engine API calls the actuator makes, through the socket proxy (D-53): list and inspect
containers, and create, start, stop, restart and remove them. A new replica copies an existing one's
configuration (image, command, mounts, lock-down, networks and aliases, compose labels with the next
container number), as `docker compose up --scale` would."""
from __future__ import annotations

import http.client
import json
import os
import urllib.parse


class Docker:
    def __init__(self, host: str | None = None):
        url = urllib.parse.urlparse(host or os.environ.get("DOCKER_HOST", "tcp://docker-proxy:2375"))
        self.host, self.port = url.hostname, url.port or 2375

    def call(self, method: str, path: str, body=None, query: dict | None = None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
        if query:
            path += "?" + urllib.parse.urlencode(query)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        r = conn.getresponse()
        data = r.read()
        if r.status >= 300:
            raise RuntimeError(f"docker {method} {path}: {r.status} {data[:300]!r}")
        return json.loads(data) if data else None

    def replicas(self, project: str, service: str) -> list[dict]:
        labels = [f"com.docker.compose.project={project}", f"com.docker.compose.service={service}"]
        found = self.call("GET", "/containers/json", query={"all": "1", "filters": json.dumps({"label": labels})})
        return sorted(found, key=lambda c: int(c["Labels"].get("com.docker.compose.container-number", "1")))

    def scale(self, project: str, service: str, n: int) -> dict:
        have = [c for c in self.replicas(project, service) if c["State"] == "running"]
        if not have:
            raise RuntimeError(f"no running replica of {service} to copy")
        before = len(have)
        if n > before:
            src = self.call("GET", f"/containers/{have[0]['Id']}/json")
            numbers = {int(c["Labels"].get("com.docker.compose.container-number", "1"))
                       for c in self.replicas(project, service)}
            for _ in range(n - before):
                k = next(i for i in range(1, 100) if i not in numbers)
                numbers.add(k)
                cfg = {key: v for key, v in src["Config"].items() if key not in ("Hostname", "Domainname")}
                cfg["Labels"] = {**cfg.get("Labels", {}), "com.docker.compose.container-number": str(k)}
                nets = {name: {"Aliases": [service]} for name in src["NetworkSettings"]["Networks"]}
                made = self.call("POST", "/containers/create", query={"name": f"{project}-{service}-{k}"},
                                 body={**cfg, "HostConfig": src["HostConfig"],
                                       "NetworkingConfig": {"EndpointsConfig": nets}})
                self.call("POST", f"/containers/{made['Id']}/start")
        for c in reversed(have[n:]):                   # scale in: the highest numbers go first
            self.call("POST", f"/containers/{c['Id']}/stop")
            self.call("DELETE", f"/containers/{c['Id']}")
        return {"before": before, "after": len([c for c in self.replicas(project, service) if c["State"] == "running"])}

    def restart(self, project: str, service: str) -> dict:
        have = [c for c in self.replicas(project, service) if c["State"] == "running"]
        for c in have:
            self.call("POST", f"/containers/{c['Id']}/restart")
        return {"restarted": len(have)}

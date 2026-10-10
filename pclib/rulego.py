"""Client for RuleGo-Server's REST API: deploy chains, execute them synchronously."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ChainFailed(Exception):
    def __init__(self, chain_id: str, status: int, body: str):
        super().__init__(f"chain {chain_id} failed (HTTP {status}): {body[:500]}")
        self.chain_id, self.status, self.body = chain_id, status, body

    @property
    def retryable(self) -> bool:
        return "exit status 75" in self.body or "Retryable" in self.body


class RuleGo:
    def __init__(self, url: str | None = None):
        self.url = (url or os.environ.get("RULEGO_URL", "http://127.0.0.1:9090")).rstrip("/")

    def _post(self, path: str, body: dict, timeout: float):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def deploy(self, chain: dict) -> str:
        cid = chain["ruleChain"]["id"]
        status, body = self._post(f"/api/v1/rules/{urllib.parse.quote(cid)}", chain, 30)
        if status != 200:
            raise ChainFailed(cid, status, body)
        return cid

    def deploy_paths(self, *paths) -> list[str]:
        files = [f for p in map(Path, paths) for f in (sorted(p.glob("*.json")) if p.is_dir() else [p])]
        return [self.deploy(json.loads(f.read_text())) for f in files]

    def execute(self, chain_id: str, data: dict, metadata: dict | None = None,
                timeout: float = 3600, msg_type: str = "STEP"):
        """Run a chain and return its final message data. Metadata goes as query parameters
        (RuleGo turns them into message metadata); a caller timeout also stops the chain."""
        q = f"?{urllib.parse.urlencode(metadata)}" if metadata else ""
        status, body = self._post(f"/api/v1/rules/{urllib.parse.quote(chain_id)}/execute/{msg_type}{q}",
                                  data, timeout)
        if status != 200:
            raise ChainFailed(chain_id, status, body)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

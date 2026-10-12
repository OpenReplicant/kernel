"""A small client for RuleGo-Server's REST API: deploy a chain, execute it synchronously."""
import os

import requests


class ChainFailed(RuntimeError):
    """A chain run failed (RuleGo answers HTTP 400 with the error text)."""


class RuleGo:
    def __init__(self, url: str | None = None, timeout: float = 60):
        self.url = (url or os.environ.get("RULEGO_URL", "http://127.0.0.1:9090")).rstrip("/")
        self.timeout = timeout

    def up(self) -> bool:
        try:
            return requests.get(f"{self.url}/api/v1/rules", timeout=2).ok
        except requests.RequestException:
            return False

    def deploy(self, chain: dict) -> str:
        """Create or replace a chain (hot; RuleGo also stores it in its data dir)."""
        cid = chain["ruleChain"]["id"]
        r = requests.post(f"{self.url}/api/v1/rules/{cid}", json=chain, timeout=self.timeout)
        if not r.ok:
            raise RuntimeError(f"deploying chain {cid} failed: HTTP {r.status_code} {r.text[:500]}")
        return cid

    def execute(self, chain_id: str, msg: dict, metadata: dict | None = None, msg_type: str = "COMMAND"):
        """Run a chain and return its final message data (parsed JSON when it is JSON)."""
        r = requests.post(f"{self.url}/api/v1/rules/{chain_id}/execute/{msg_type}",
                          params=metadata or {}, json=msg, timeout=self.timeout)
        if r.status_code != 200:
            raise ChainFailed(f"chain {chain_id}: HTTP {r.status_code}: {r.text}")
        try:
            return r.json()
        except ValueError:
            return r.text

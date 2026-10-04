"""The explorer's smoke check, against a running stack started with `make up-ui` and seeded
with `make seed`:

1. The API is a reader's view: writes are refused at Caddy and in the database, and the
   unmasked claim text and log operations stay out of reach.
2. Every page loads in headless Chromium with no failed API call, no script error and no
   error view; an unknown page and an unknown id show the error view.

    uv run --with playwright==1.56.0 python ui/smoke.py --url http://localhost:8080
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

PLAYWRIGHT = "playwright==1.56.0"


class Smoke:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.http = httpx.Client(base_url=self.url, timeout=15)
        self.failures: list[str] = []

    def check(self, ok: bool, what: str) -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            self.failures.append(what)

    def wait(self, seconds: float = 60) -> None:
        """PostgREST loads its schema cache after the database is up; wait until it answers."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                if self.http.get("/api/rpc/head_offset").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                sys.exit(f"the explorer at {self.url} did not answer within {seconds:.0f}s (make up-ui)")
            time.sleep(1)

    def get(self, path: str) -> Any:
        res = self.http.get(f"/api/{path}")
        res.raise_for_status()
        return res.json()

    # 1. The API ------------------------------------------------------------------------

    def api(self) -> None:
        page = self.http.get("/")
        self.check(page.status_code == 200 and "x-data" in page.text, "GET / serves the explorer")
        self.check("default-src 'self'" in page.headers.get("content-security-policy", ""), "CSP header set")
        self.check(isinstance(self.get("rpc/version"), str), "GET /api/rpc/version answers")
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            res = self.http.request(method, "/api/nodes", json={"name": "x"})
            self.check(res.status_code == 405, f"{method} /api/nodes is refused by Caddy ({res.status_code})")
        writes = {
            "write": {"payload": {}, "p_agent_id": "agt_x"},
            "ingest_source": {"p_source": {}, "p_agent_id": "agt_x"},
            "cite": {"p_cite": {}, "p_agent_id": "agt_x"},
        }
        for fn, args in writes.items():
            res = self.http.post(f"/api/rpc/{fn}", json=args)
            self.check(
                res.status_code in (401, 403) and "permission denied" in res.text,
                f"POST /api/rpc/{fn} is refused by the database ({res.status_code})",
            )
        for path in ("claims?select=text&limit=1", "log?select=ops&limit=1"):
            res = self.http.get(f"/api/{path}")
            self.check(
                res.status_code in (401, 403) and "permission denied" in res.text,
                f"GET /api/{path} is refused: readers see masked views only ({res.status_code})",
            )

    # 2. Pages --------------------------------------------------------------------------

    def routes(self) -> list[tuple[str, str, str]]:
        """(route, the route it settles on, expected state) for every page, with ids from the data."""
        entity = self.get("nodes?select=id&type=eq.Entity&order=created_offset&limit=1")
        claim = self.get("nodes?select=id&type=eq.Claim&order=created_offset&limit=1")
        edge = self.get("edges?select=id&order=created_offset&limit=1")
        contested = self.get("edges?select=id&belief_status=eq.contested&limit=1")
        source = self.get("sources?select=id&order=recorded_at&limit=1")
        head = int(self.get("rpc/head_offset"))
        self.check(bool(entity and claim and edge and source), "the stack has data (run make seed)")
        if not entity:
            return []
        area = self.get(f"nodes?select=namespace&id=eq.{entity[0]['id']}")[0]["namespace"]
        pages = ["#/", "#/search/a", "#/log", f"#/log/{max(head - 5, 1)}", "#/evidence", "#/ontology"]
        pages += ["#/models", "#/graph", f"#/graph/ns:{area}"]
        pages += [f"#/graph/{n['id']}" for n in entity] + [f"#/graph/{n['id']}/1" for n in claim]
        pages += [f"#/node/{n['id']}" for n in entity + claim]
        pages += [f"#/edge/{e['id']}" for e in edge + contested]
        pages += [f"#/source/{s['id']}" for s in source]
        routes = [(r, r, "ready") for r in pages]
        routes += [(f"#/search/{s['id']}", f"#/source/{s['id']}", "ready") for s in source]
        routes += [(r, r, "error") for r in ("#/nowhere", "#/node/ent_00000000000000000000000000")]
        return routes

    def pages(self) -> None:
        try:
            from playwright.sync_api import ConsoleMessage, Response, sync_playwright
        except ImportError:
            sys.exit(f"the page checks need Playwright: uv run --with {PLAYWRIGHT} python ui/smoke.py")
        routes = self.routes()
        problems: list[str] = []

        def on_console(m: ConsoleMessage) -> None:
            if m.type in ("error", "warning"):
                problems.append(f"console {m.type}: {m.text}")

        def on_response(r: Response) -> None:
            if "/api/" in r.url and r.status >= 400:
                problems.append(f"{r.status} {r.request.method} {r.url}")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            for scheme in ("light", "dark"):
                page = browser.new_page(color_scheme=scheme)
                page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
                page.on("console", on_console)
                page.on("response", on_response)
                page.goto(f"{self.url}/")
                for route, settles, expected in routes:
                    problems.clear()
                    page.evaluate("route => { location.hash = route }", route)
                    try:
                        page.wait_for_function(
                            "route => document.body.dataset.state && document.body.dataset.route === route",
                            arg=settles,
                            timeout=15_000,
                        )
                    except Exception:
                        problems.append("did not finish loading")
                    state = page.evaluate("document.body.dataset.state")
                    if not page.locator("h1").first.inner_text().strip():
                        problems.append("empty heading")
                    if route.startswith("#/graph") and not page.locator("#graph canvas").count():
                        problems.append("no graph drawn")
                    self.check(
                        state == expected and not problems,
                        f"{scheme:<5} {route} -> {state}{'; ' + '; '.join(problems) if problems else ''}",
                    )
                page.close()
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-check the explorer against a running stack.")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--api-only", action="store_true", help="skip the browser checks")
    args = parser.parse_args()
    smoke = Smoke(args.url)
    smoke.wait()
    smoke.api()
    if not args.api_only:
        smoke.pages()
    if smoke.failures:
        sys.exit(f"{len(smoke.failures)} explorer check(s) failed")
    print("explorer: all checks passed")


if __name__ == "__main__":
    main()

"""The UI's HTTP routes, served by `kk serve` next to /mcp (T20, D-44).

  /ui/              the shell (modules/ui/web): an MCP client; every call carries the person's own token
  /ui/views.json    the view modules: manifests with runtime type `view` that fill the `ui` slot
  /ui/views/<name>/ each view module's folder (its entrypoint script registers the view with the shell)

Static files only; nothing here reads the database. Responses carry a strict Content-Security-Policy
(scripts from this origin only; Alpine.js needs 'unsafe-eval' for its expressions).
"""
from __future__ import annotations

from pathlib import Path

import yaml

CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
HEADERS = [(b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
           (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-cache")]


def views(root: Path) -> list[dict]:
    out = []
    for manifest in sorted(root.glob("modules/*/module.yaml")):
        m = yaml.safe_load(manifest.read_text())
        rt = m.get("runtime", {})
        if rt.get("type") != "view" or "ui" not in m.get("slots", []) or manifest.parent.name == "ui":
            continue
        entry = Path(rt["entrypoint"])
        out.append({"name": manifest.parent.name, "title": m["name"], "module": m["id"],
                    "version": str(m["version"]), "dir": manifest.parent / entry.parent,
                    "src": f"/ui/views/{manifest.parent.name}/{entry.name}"})
    return out


class Secure:
    """ASGI wrapper adding the security headers to every response of the wrapped app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *HEADERS]}
            await send(message)
        await self.app(scope, receive, send_with_headers)


def routes(root: Path) -> list:
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    shell = root / "modules/ui/web"
    if not shell.is_dir():
        return []
    found = views(root)
    listing = [{k: v for k, v in view.items() if k != "dir"} for view in found]

    async def views_json(request):
        return JSONResponse(listing, headers=dict((k.decode(), v.decode()) for k, v in HEADERS))

    async def home(request):
        return RedirectResponse("/ui/")

    return [Route("/", home), Route("/ui", home), Route("/ui/views.json", views_json),
            *[Mount(f"/ui/views/{v['name']}", app=Secure(StaticFiles(directory=v["dir"]))) for v in found],
            Mount("/ui", app=Secure(StaticFiles(directory=shell, html=True)))]

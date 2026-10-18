"""The UI's HTTP routes, served by `kk serve` next to /mcp (T20, D-44).

  /ui/              the shell (modules/ui/web): an MCP client; every call carries the person's own token
  /ui/views.json    the view modules: manifests with runtime type `view` that fill the `ui` slot
  /ui/views/<name>/ each view module's folder (its entrypoint script registers the view with the shell)
  /ui/login         POST {name, password}: a person signs in with their own Postgres login (`kk person add`);
                    Postgres checks the password, and the answer is a bearer token for their role (D-73)

Static files otherwise; only the sign-in touches the database, as the person. Responses carry a strict Content-Security-Policy
(scripts from this origin only; Alpine.js needs 'unsafe-eval' for its expressions).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import yaml

SESSION_HOURS = 8
PERSON = "kk_reviewer"   # people are reviewers (kk person add); agents and modules sign in elsewhere

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


async def sign_in(name: str, password: str, url: str) -> str | None:
    """The person's role if Postgres accepts their password and the role is a person's; else None."""
    import psycopg
    from psycopg.conninfo import make_conninfo
    role = "person_" + name.replace("-", "_")
    try:
        async with await psycopg.AsyncConnection.connect(make_conninfo(url, user=role, password=password),
                                                         connect_timeout=10) as conn:
            row = await (await conn.execute(
                "select pg_has_role(current_user, %s, 'member') and not rolsuper"
                " from pg_roles where rolname = current_user", (PERSON,))).fetchone()
    except psycopg.Error:
        return None
    return role if row and row[0] else None


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

    async def login(request):
        from .cli import NAME, mint
        headers = dict((k.decode(), v.decode()) for k, v in HEADERS)
        try:
            body = await request.json()
            name, password = str(body["name"]), str(body["password"])
        except Exception:
            return JSONResponse({"error": "send {name, password}"}, status_code=400, headers=headers)
        secret, url = os.environ.get("KK_JWT_SECRET", ""), os.environ.get("DATABASE_URL", "")
        if len(secret) < 32 or not url:
            return JSONResponse({"error": "sign-in is not configured"}, status_code=503, headers=headers)
        role = await sign_in(name, password, url) if NAME.match(name) and password else None
        if not role:
            await asyncio.sleep(1)   # slows guessing; Postgres logs every failed attempt
            return JSONResponse({"error": "name or password not accepted"}, status_code=401, headers=headers)
        return JSONResponse({"token": mint(role, secret, SESSION_HOURS / 24), "role": role,
                             "expires": int(time.time()) + SESSION_HOURS * 3600}, headers=headers)

    return [Route("/", home), Route("/ui", home), Route("/ui/views.json", views_json),
            Route("/ui/login", login, methods=["POST"]),
            *[Mount(f"/ui/views/{v['name']}", app=Secure(StaticFiles(directory=v["dir"]))) for v in found],
            Mount("/ui", app=Secure(StaticFiles(directory=shell, html=True)))]

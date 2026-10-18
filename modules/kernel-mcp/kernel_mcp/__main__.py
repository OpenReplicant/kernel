"""Run kernel-mcp.

  python -m kernel_mcp            stdio, one agent: KK_DATABASE_URL = the agent's own login (any MCP harness)
  python -m kernel_mcp --http     streamable HTTP at /mcp (stateless, JSON responses), many agents:
                                  DATABASE_URL (kk_authenticator) + KK_JWT_SECRET; each request carries its own bearer JWT

HTTP env: PORT (default 8000), KK_MCP_ALLOWED_HOSTS (comma-separated Host values accepted, for
DNS-rebinding protection; default: localhost and 127.0.0.1 on any port, and kernel:8000), KK_SITE (the public
host name Caddy serves over HTTPS, deploy/public.yaml: accepted as a Host and as https://KK_SITE an Origin).
`kk serve` runs this together with the validator and the belief model.
"""
import os
import sys

from . import server


def http(host: str = "0.0.0.0", port: int | None = None, extra_routes: list | None = None) -> None:
    """extra_routes: Starlette routes served before /mcp (kk serve adds the UI)."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    server.REQUIRE_TOKEN = True
    hosts = os.environ.get("KK_MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,kernel:8000").split(",")
    origins = ["http://localhost:*", "http://127.0.0.1:*"]
    if site := os.environ.get("KK_SITE", "").strip():
        hosts += [site, f"{site}:*"]
        origins += [f"https://{site}", f"https://{site}:*"]
    app = server.mcp.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0",
        transport_security=TransportSecuritySettings(allowed_hosts=[h.strip() for h in hosts if h.strip()],
                                                     allowed_origins=origins))
    if extra_routes:
        app.router.routes[0:0] = extra_routes
    uvicorn.run(app, host=host, port=port or int(os.environ.get("PORT", "8000")), log_level="info")


def main() -> None:
    if "--http" in sys.argv[1:]:
        http()
    else:
        server.mcp.run("stdio")


if __name__ == "__main__":
    main()

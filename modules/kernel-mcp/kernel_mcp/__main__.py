"""Serve kernel-mcp over streamable HTTP at /mcp (stateless, JSON responses).

Env: KK_API, PORT (default 8000), KK_MCP_ALLOWED_HOSTS (comma-separated Host values accepted, for
DNS-rebinding protection; default: localhost and 127.0.0.1 on any port, and kernel-mcp:8000).
"""
import os

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from .server import mcp

hosts = os.environ.get("KK_MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,kernel-mcp:8000").split(",")
app = mcp.streamable_http_app(
    stateless_http=True, json_response=True, host="0.0.0.0",
    transport_security=TransportSecuritySettings(allowed_hosts=[h.strip() for h in hosts if h.strip()],
                                                 allowed_origins=["http://localhost:*", "http://127.0.0.1:*"]))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")

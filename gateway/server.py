"""The MCP gateway: one server exposing the seven kernel tools."""

from __future__ import annotations

import logging

import anyio
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from gateway import otel, profiles
from gateway.config import Settings
from gateway.db import Kernel
from gateway.embeddings import Embedder, HttpEmbedder, NoEmbedder
from gateway.identity import Agents, ensure_agents
from gateway.tools import Tools

log = logging.getLogger(__name__)

VERSION = "0.1.0"

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def instructions(agents: Agents, profile: str) -> str:
    lines = [
        "World Model Kernel: a sourced, versioned world model. Follow the core skill:",
        "read first (get_schema_slice, lookup_entities, query_graph), then write one claim at a time with",
        "the ops it justifies and the head_offset of your last read as read_at_offset. Fix rejected writes",
        "using the problem document; after two failed retries write the claim with empty ops (unresolved).",
        "Ingest sources before citing them, and cite the edges and claims each answer sentence relied on.",
        f"You write as agent {agents.agent_id} (profile {profile}).",
    ]
    if agents.person_id:
        lines.append(
            f"The person you work with is agent {agents.person_id}: with their consent, ingest their turns "
            "with author set to it (interview skill)."
        )
    return "\n".join(lines)


def build_server(tools: Tools) -> MCPServer:
    server = MCPServer(
        "wmk",
        title="World Model Kernel",
        instructions=instructions(tools.agents, tools.profile),
        version=VERSION,
    )
    server.add_tool(tools.write, name="write", annotations=_WRITE)
    server.add_tool(tools.lookup_entities, name="lookup_entities", annotations=_READ)
    server.add_tool(tools.get_schema_slice, name="get_schema_slice", annotations=_READ)
    server.add_tool(tools.query_graph, name="query_graph", annotations=_READ)
    server.add_tool(tools.query_log, name="query_log", annotations=_READ)
    server.add_tool(tools.ingest_source, name="ingest_source", annotations=_WRITE)
    server.add_tool(tools.cite, name="cite", annotations=_WRITE)

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "head_offset": await tools.kernel.head_offset()})

    return server


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_url and settings.embedding_model:
        return HttpEmbedder(settings.embedding_url, settings.embedding_model, settings.embedding_api_key)
    return NoEmbedder()


async def serve(settings: Settings) -> None:
    profile = profiles.load(settings.profile_path)
    otel.setup(profile.name)
    kernel = Kernel(settings.writer_dsn, settings.reader_dsn)
    await kernel.open()
    try:
        agents = await ensure_agents(kernel, profile)
        log.info("gateway ready: profile %s, agent %s", profile.name, agents.agent_id)
        server = build_server(Tools(kernel, make_embedder(settings), agents, profile.name))
        if settings.transport == "stdio":
            await server.run_stdio_async()
        else:
            await server.run_streamable_http_async(host=settings.host, port=settings.port)
    finally:
        await kernel.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    anyio.run(serve, Settings.from_env())


if __name__ == "__main__":
    main()

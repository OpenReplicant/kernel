"""The paper-source MCP server: `search_papers` and `get_paper`.

Environment: `WMK_PAPERS_OPENALEX_API_KEY` (enables OpenAlex), `WMK_PAPERS_MAILTO` (a
contact email for the Crossref and OpenAlex polite pools), `WMK_PAPERS_TRANSPORT`
(`streamable-http` or `stdio`), `WMK_PAPERS_HOST`, `WMK_PAPERS_PORT` (default 8001).
Failures are RFC 9457 problem documents under `urn:wmk:papers:`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from wmk_papers.records import normalize_arxiv, normalize_doi
from wmk_papers.sources import ArxivSource, CrossrefSource, NotFound, OpenAlexSource, Source

log = logging.getLogger(__name__)

SourceName = Literal["auto", "openalex", "crossref", "arxiv"]
_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def problem(kind: str, title: str, status: int, detail: str) -> CallToolResult:
    doc = {"type": f"urn:wmk:papers:{kind}", "title": title, "status": status, "detail": detail}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(doc))], is_error=True)


def ok(result: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, default=str))], structured_content=result
    )


@dataclass
class Papers:
    sources: dict[str, Source]

    def _pick(self, source: str) -> list[Source]:
        """Sources to try, in order. auto: OpenAlex when configured, then Crossref; arXiv
        answers searches only when asked for, and lookups of arXiv ids."""
        if source != "auto":
            return [self.sources[source]] if source in self.sources else []
        return [self.sources[n] for n in ("openalex", "crossref") if n in self.sources]

    async def search_papers(
        self,
        query: Annotated[str, Field(description="Words to search titles and abstracts for.", min_length=2)],
        limit: Annotated[int, Field(ge=1, le=50, description="Papers to return.")] = 10,
        source: Annotated[
            SourceName,
            Field(description="auto: OpenAlex when configured, else Crossref. arxiv: preprints on arXiv."),
        ] = "auto",
        year_from: Annotated[
            int | None, Field(ge=1800, le=2100, description="Only papers from this year on.")
        ] = None,
    ) -> CallToolResult:
        """Find papers. Each result has title, authors (with ORCID iDs when known), year, venue, DOI,
        arXiv id and a shortened abstract. Call get_paper for the full record and the arguments to
        ingest it."""
        tried = self._pick(source)
        if not tried:
            return problem("unavailable", "Source not configured", 400, f"{source} is not configured here")
        for s in tried:
            try:
                found = await anyio.to_thread.run_sync(s.search, query, limit, year_from)
            except Exception as exc:
                log.warning("%s search failed: %s", s.name, type(exc).__name__)
                continue
            return ok({"source": s.name, "results": [p.summary() for p in found]})
        names = ", ".join(s.name for s in tried)
        return problem("upstream", "Paper source failed", 502, f"{names} failed; try again")

    async def get_paper(
        self,
        doi: Annotated[str | None, Field(description="A DOI, doi: string or doi.org URL.")] = None,
        arxiv_id: Annotated[str | None, Field(description="An arXiv id or arxiv.org URL.")] = None,
        source: SourceName = "auto",
    ) -> CallToolResult:
        """One paper's full record (abstract, references) and `ingest`: the exact ingest_source
        arguments for it (Markdown with title, authors, venue, year, ids and abstract; uri;
        collection doi:<doi> or arxiv:<id>; metadata), as the research skill describes."""
        wanted_doi, wanted_arxiv = normalize_doi(doi), normalize_arxiv(arxiv_id)
        if not wanted_doi and not wanted_arxiv:
            return problem("input", "No identifier", 400, "give a DOI or an arXiv id")
        if source == "auto":
            names = (["openalex", "crossref"] if wanted_doi else []) + (["arxiv"] if wanted_arxiv else [])
            tried = [self.sources[n] for n in names if n in self.sources]
        else:
            tried = self._pick(source)
        failures = []
        for s in tried:
            try:
                paper = await anyio.to_thread.run_sync(s.get, wanted_doi, wanted_arxiv)
            except NotFound as exc:
                failures.append(str(exc))
                continue
            except Exception as exc:
                log.warning("%s lookup failed: %s", s.name, type(exc).__name__)
                failures.append(f"{s.name} failed")
                continue
            return ok({"source": s.name, "paper": paper.full(), "ingest": paper.ingest_arguments()})
        if failures and all(not f.endswith("failed") for f in failures):
            return problem("not-found", "Paper not found", 404, "; ".join(failures))
        return problem("upstream", "Paper source failed", 502, "; ".join(failures) or "no source configured")


def build_server(papers: Papers) -> MCPServer:
    server = MCPServer(
        "papers",
        title="Paper sources (research pack)",
        instructions=(
            "Finds papers on OpenAlex, Crossref and arXiv. Use search_papers to find candidates, "
            "get_paper for one paper's full record, then pass its `ingest` arguments to the World "
            "Model Kernel's ingest_source and map it with the research skill."
        ),
    )
    server.add_tool(papers.search_papers, name="search_papers", annotations=_READ)
    server.add_tool(papers.get_paper, name="get_paper", annotations=_READ)

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "sources": sorted(papers.sources)})

    return server


def sources_from_env() -> dict[str, Source]:
    mailto = os.environ.get("WMK_PAPERS_MAILTO") or None
    found: dict[str, Source] = {"crossref": CrossrefSource(mailto), "arxiv": ArxivSource()}
    if key := os.environ.get("WMK_PAPERS_OPENALEX_API_KEY"):
        found["openalex"] = OpenAlexSource(key, mailto)
    return found


async def serve() -> None:
    server = build_server(Papers(sources_from_env()))
    if os.environ.get("WMK_PAPERS_TRANSPORT", "streamable-http") == "stdio":
        await server.run_stdio_async()
    else:
        host = os.environ.get("WMK_PAPERS_HOST", "0.0.0.0")
        await server.run_streamable_http_async(host=host, port=int(os.environ.get("WMK_PAPERS_PORT", "8001")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    anyio.run(serve)


if __name__ == "__main__":
    main()

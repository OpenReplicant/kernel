"""The research pack's paper-source server: identifiers, records, the three sources over
recorded responses (no network), the MCP tools, and that a paper's ingest arguments are
accepted by the kernel as the research skill expects."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import habanero.request
import httpx2
import pytest
import requests
from mcp import Client

from wmk_papers.records import (
    Author,
    Paper,
    clean_text,
    normalize_arxiv,
    normalize_doi,
    normalize_orcid,
)
from wmk_papers.server import Papers, build_server
from wmk_papers.sources import ArxivSource, CrossrefSource, NotFound, OpenAlexSource, Throttle

RECORDED = Path(__file__).resolve().parent / "recorded"


@pytest.mark.parametrize(
    ("value", "doi"),
    [
        ("10.5555/WMK.RGD.2025", "10.5555/wmk.rgd.2025"),
        ("https://doi.org/10.5555/wmk.rgd.2025", "10.5555/wmk.rgd.2025"),
        ("doi:10.1000/xyz123.", "10.1000/xyz123"),
        ("not a doi", None),
    ],
)
def test_doi_normalisation(value: str, doi: str | None) -> None:
    assert normalize_doi(value) == doi


@pytest.mark.parametrize(
    ("value", "arxiv_id"),
    [
        ("2501.01234v2", "2501.01234"),
        ("https://arxiv.org/abs/2501.01234v1", "2501.01234"),
        ("https://arxiv.org/pdf/2501.01234v3.pdf", "2501.01234"),
        ("arXiv:2501.01234", "2501.01234"),
        ("10.48550/arXiv.2501.01234", "2501.01234"),
        ("hep-th/9901001v2", "hep-th/9901001"),
        ("nothing here", None),
    ],
)
def test_arxiv_normalisation(value: str, arxiv_id: str | None) -> None:
    assert normalize_arxiv(value) == arxiv_id


def test_orcid_and_abstract_cleaning() -> None:
    assert normalize_orcid("http://orcid.org/0000-0002-1825-009x") == "0000-0002-1825-009X"
    assert clean_text("<jats:title>Abstract</jats:title><jats:p>A &amp; B\n  c.</jats:p>") == "A & B c."


def paper() -> Paper:
    return Paper(
        title="Retrieval-Gated Decoding Reduces Hallucination in Abstractive Summarization",
        authors=[Author("Ana Lima", "0000-0002-1825-0097"), Author("Ben Okafor")],
        year=2025,
        venue="Synthetic Proceedings of Summarization 12",
        doi="10.5555/wmk.rgd.2025",
        abstract="We introduce retrieval-gated decoding (RGD).",
        found_in="crossref",
    )


def test_ingest_arguments_follow_the_research_skill() -> None:
    args = paper().ingest_arguments()
    assert args["uri"] == "https://doi.org/10.5555/wmk.rgd.2025"
    assert args["collection"] == "doi:10.5555/wmk.rgd.2025"
    assert args["media_type"] == "text/markdown" and args["title"].startswith("Retrieval-Gated")
    assert args["metadata"] == {
        "doi": "10.5555/wmk.rgd.2025",
        "year": 2025,
        "venue": "Synthetic Proceedings of Summarization 12",
    }
    assert args["content"].splitlines()[:3] == [
        "# Retrieval-Gated Decoding Reduces Hallucination in Abstractive Summarization",
        "",
        "Ana Lima, Ben Okafor. 2025. Synthetic Proceedings of Summarization 12. doi:10.5555/wmk.rgd.2025",
    ]
    preprint = Paper(title="T", authors=[], arxiv="2501.01234")
    assert preprint.ingest_arguments()["collection"] == "arxiv:2501.01234"
    assert preprint.ingest_arguments()["uri"] == "https://arxiv.org/abs/2501.01234"


@pytest.mark.anyio
async def test_the_kernel_accepts_a_paper_as_two_chunks(dbname: str) -> None:
    from kernel.testing import gateway_client

    async with gateway_client(dbname) as client:
        result = await client.call_tool("ingest_source", paper().ingest_arguments())
        body = json.loads(result.content[0].text)
    assert not result.is_error, body
    assert [c["text"].splitlines()[0] for c in body["chunks"]] == [
        "# Retrieval-Gated Decoding Reduces Hallucination in Abstractive Summarization",
        "## Abstract",
    ]


# Recorded responses ---------------------------------------------------------------------


def recorded(name: str) -> bytes:
    return (RECORDED / name).read_bytes()


def fake_requests(monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, bytes]]) -> list[str]:
    """Serve recorded bodies to requests-based libraries (arxiv, pyalex) by URL substring."""
    seen: list[str] = []

    def get(self: requests.Session, url: str, **_: Any) -> requests.Response:
        seen.append(url)
        response = requests.Response()
        response.url = url
        for part, (status, body) in routes.items():
            if part in url:
                response.status_code, response._content = status, body
                return response
        response.status_code, response._content = 404, b'{"error": "not recorded"}'
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    return seen


def fake_httpx2(monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, bytes]]) -> list[str]:
    """Serve recorded bodies to habanero, which calls httpx2.get."""
    seen: list[str] = []

    def get(url: str, params: dict[str, Any] | None = None, **_: Any) -> httpx2.Response:
        seen.append(url)
        request = httpx2.Request("GET", url, params=params)
        for part, (status, body) in routes.items():
            if part in url:
                return httpx2.Response(
                    status, content=body, headers={"content-type": "application/json"}, request=request
                )
        return httpx2.Response(
            404, content=b"Resource not found.", headers={"content-type": "text/plain"}, request=request
        )

    monkeypatch.setattr(habanero.request.httpx2, "get", get)
    return seen


def test_arxiv_source_reads_the_atom_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_requests(monkeypatch, {"export.arxiv.org": (200, recorded("arxiv_entry.xml"))})
    found = ArxivSource().get(None, "https://arxiv.org/abs/2501.01234v2")
    assert "id_list=2501.01234" in seen[0]
    assert found.title == "Retrieval-Gated Decoding Reduces Hallucination in Abstractive Summarization"
    assert [a.name for a in found.authors] == ["Ana Lima", "Ben Okafor"]
    assert (found.arxiv, found.doi, found.year) == ("2501.01234", "10.5555/wmk.rgd.2025", 2025)
    assert found.venue == "Synthetic Proceedings of Summarization 12"
    assert found.abstract and found.abstract.startswith("Abstractive summarizers often state facts")
    assert "\n" not in found.abstract
    assert ArxivSource().search("retrieval gating", 5, year_from=2026) == []


def test_openalex_source_reads_a_work(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_requests(monkeypatch, {"api.openalex.org": (200, recorded("openalex_work.json"))})
    found = OpenAlexSource("key", throttle=Throttle(0)).get("10.5555/wmk.rgd.2026a", None)
    assert "api.openalex.org/works/https://doi.org/10.5555/wmk.rgd.2026a" in unquote(seen[0])
    assert found.doi == "10.5555/wmk.rgd.2026a" and found.year == 2026
    assert [(a.name, a.orcid) for a in found.authors] == [("Chen Wu", "0000-0002-1825-0097")]
    assert found.abstract == "We test RGD on GovLong. It did not RGD help."
    assert found.venue == "Synthetic Journal of Text Generation" and found.references == ["W0000000002"]


def test_openalex_source_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_requests(monkeypatch, {})
    with pytest.raises(NotFound):
        OpenAlexSource("key", throttle=Throttle(0)).get("10.5555/unknown", None)


def test_crossref_source_reads_a_work(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_httpx2(monkeypatch, {"/works/10.5555/wmk.rgd.2026b": (200, recorded("crossref_work.json"))})
    found = CrossrefSource("tests@example.test", throttle=Throttle(0)).get("doi:10.5555/WMK.RGD.2026B", None)
    assert seen and found.title == "Retrieval-Gated Decoding for Clinical Note Summarization"
    assert [(a.name, a.orcid) for a in found.authors] == [
        ("Ana Lima", "0000-0002-1825-0097"),
        ("Dev Patel", None),
    ]
    assert found.year == 2026 and found.venue == "Synthetic Workshop on Clinical Text"
    assert (
        found.abstract
        == "We apply retrieval-gated decoding to summaries of clinical notes & report factual consistency."
    )
    assert found.references == ["10.5555/wmk.rgd.2025"]


def test_crossref_source_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_httpx2(monkeypatch, {})
    with pytest.raises(NotFound):
        CrossrefSource(throttle=Throttle(0)).get("10.5555/unknown", None)


def test_throttle_spaces_requests() -> None:
    throttle = Throttle(0.05)
    started = time.monotonic()
    for _ in range(3):
        throttle.wait()
    assert time.monotonic() - started >= 0.1


# The MCP tools ----------------------------------------------------------------------------


class FakeSource:
    def __init__(self, name: str, papers: dict[str, Paper], fail: bool = False) -> None:
        self.name, self.papers, self.fail = name, papers, fail

    def search(self, query: str, limit: int, year_from: int | None) -> list[Paper]:
        if self.fail:
            raise RuntimeError("upstream down")
        return [p for p in self.papers.values() if query.lower() in p.title.lower()][:limit]

    def get(self, doi: str | None, arxiv_id: str | None) -> Paper:
        if self.fail:
            raise RuntimeError("upstream down")
        if doi in self.papers:
            return self.papers[doi]
        raise NotFound(f"no {self.name} record for {doi}")


async def call(client: Any, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await client.call_tool(tool, args)
    return bool(result.is_error), json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_tools_search_fetch_and_fall_back() -> None:
    known = {"10.5555/wmk.rgd.2025": paper()}
    papers = Papers(
        {"openalex": FakeSource("openalex", {}, fail=True), "crossref": FakeSource("crossref", known)}
    )
    async with Client(build_server(papers)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"search_papers", "get_paper"}

        error, found = await call(client, "search_papers", {"query": "retrieval-gated"})
        assert not error and found["source"] == "crossref" and len(found["results"]) == 1

        error, got = await call(client, "get_paper", {"doi": "https://doi.org/10.5555/WMK.RGD.2025"})
        assert not error and got["paper"]["doi"] == "10.5555/wmk.rgd.2025"
        assert got["ingest"]["collection"] == "doi:10.5555/wmk.rgd.2025"

        error, missing = await call(client, "get_paper", {"doi": "10.5555/unknown", "source": "crossref"})
        assert error and missing["type"] == "urn:wmk:papers:not-found" and missing["status"] == 404

        error, bad = await call(client, "get_paper", {})
        assert error and bad["type"] == "urn:wmk:papers:input"

        error, unconfigured = await call(client, "search_papers", {"query": "rgd", "source": "arxiv"})
        assert error and unconfigured["type"] == "urn:wmk:papers:unavailable"

    down = Papers({"crossref": FakeSource("crossref", known, fail=True)})
    async with Client(build_server(down)) as client:
        error, failed = await call(client, "get_paper", {"doi": "10.5555/wmk.rgd.2025"})
        assert error and failed["type"] == "urn:wmk:papers:upstream" and failed["status"] == 502

"""Paper sources: arXiv (`arxiv`), OpenAlex (`pyalex`) and Crossref (`habanero`).

The libraries do the HTTP, pagination and retries; this module maps their records to
`Paper` and spaces requests to each API's published limits. Calls are blocking: the
server runs them in worker threads.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import arxiv
import habanero
import pyalex

from wmk_papers.records import (
    Author,
    Paper,
    clean_text,
    normalize_arxiv,
    normalize_doi,
    normalize_orcid,
)


class NotFound(Exception):
    """The source has no record for the identifier."""


class Source(Protocol):
    name: str

    def search(self, query: str, limit: int, year_from: int | None) -> list[Paper]: ...

    def get(self, doi: str | None, arxiv_id: str | None) -> Paper: ...


def is_not_found(exc: BaseException) -> bool:
    """A 404 from any of the libraries: requests' and httpx2's HTTP errors carry the response,
    habanero's RequestError the status code."""
    response = getattr(exc, "response", None)
    return 404 in (getattr(response, "status_code", None), getattr(exc, "status_code", None))


class Throttle:
    """At most one request per `interval` seconds, across threads."""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.interval = interval
        self._clock = clock
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class ArxivSource:
    """arXiv's API: at most one request every three seconds (the library enforces it)."""

    name = "arxiv"

    def __init__(self, client: arxiv.Client | None = None) -> None:
        self._client = client or arxiv.Client(page_size=50, delay_seconds=3.0, num_retries=2)

    def search(self, query: str, limit: int, year_from: int | None) -> list[Paper]:
        found = self._client.results(arxiv.Search(query=query, max_results=limit))
        papers = [self._paper(r) for r in found]
        return [p for p in papers if not year_from or (p.year or 0) >= year_from]

    def get(self, doi: str | None, arxiv_id: str | None) -> Paper:
        wanted = normalize_arxiv(arxiv_id) or normalize_arxiv(doi)
        if not wanted:
            raise NotFound("arXiv looks papers up by arXiv id")
        for result in self._client.results(arxiv.Search(id_list=[wanted], max_results=1)):
            return self._paper(result)
        raise NotFound(f"no arXiv record for {wanted}")

    @staticmethod
    def _paper(r: arxiv.Result) -> Paper:
        return Paper(
            title=clean_text(r.title) or "",
            authors=[Author(name=a.name) for a in r.authors],
            year=r.published.year if r.published else None,
            venue=r.journal_ref or "arXiv",
            doi=normalize_doi(r.doi),
            arxiv=normalize_arxiv(r.get_short_id()),
            abstract=clean_text(r.summary),
            url=r.entry_id,
            found_in="arxiv",
            pdf_url=r.pdf_url,
        )


class OpenAlexSource:
    """OpenAlex: needs an API key (free) since February 2026; ten requests a second at most."""

    name = "openalex"

    def __init__(self, api_key: str, email: str | None = None, throttle: Throttle | None = None) -> None:
        pyalex.config.api_key = api_key
        pyalex.config.email = email
        pyalex.config.max_retries = 2
        self._throttle = throttle or Throttle(0.1)

    def search(self, query: str, limit: int, year_from: int | None) -> list[Paper]:
        works = pyalex.Works().search(query)
        if year_from:
            works = works.filter(from_publication_date=f"{year_from}-01-01")
        self._throttle.wait()
        return [self._paper(w) for w in works.get(per_page=limit)]

    def get(self, doi: str | None, arxiv_id: str | None) -> Paper:
        wanted = normalize_doi(doi) or (f"10.48550/arxiv.{normalize_arxiv(arxiv_id)}" if arxiv_id else None)
        if not wanted:
            raise NotFound("OpenAlex looks papers up by DOI or arXiv id")
        self._throttle.wait()
        try:
            return self._paper(pyalex.Works()[f"https://doi.org/{wanted}"])
        except Exception as exc:
            if is_not_found(exc):
                raise NotFound(f"no OpenAlex record for {wanted}") from exc
            raise

    @staticmethod
    def _paper(w: dict[str, Any]) -> Paper:
        location = w.get("primary_location") or {}
        venue = (location.get("source") or {}).get("display_name")
        ids = w.get("ids") or {}
        doi = normalize_doi(w.get("doi") or ids.get("doi"))
        return Paper(
            title=clean_text(w.get("display_name") or w.get("title")) or "",
            authors=[
                Author(
                    name=(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "",
                    orcid=normalize_orcid((a.get("author") or {}).get("orcid")),
                    openalex=((a.get("author") or {}).get("id") or "").rsplit("/", 1)[-1] or None,
                )
                for a in w.get("authorships") or []
            ],
            year=w.get("publication_year"),
            venue=venue,
            doi=doi,
            arxiv=normalize_arxiv(doi) if doi and doi.startswith("10.48550/") else None,
            abstract=clean_text(pyalex.invert_abstract(w.get("abstract_inverted_index"))),
            url=w.get("id"),
            found_in="openalex",
            cited_by=w.get("cited_by_count"),
            references=[r.rsplit("/", 1)[-1] for r in w.get("referenced_works") or []],
            pdf_url=(w.get("best_oa_location") or {}).get("pdf_url") or location.get("pdf_url"),
        )


class CrossrefSource:
    """Crossref: no key; its polite pool (with a contact email) allows ten requests a second
    for single records and three for lists. Abstracts are present only when publishers
    deposit them."""

    name = "crossref"

    def __init__(self, mailto: str | None = None, throttle: Throttle | None = None) -> None:
        self._cr = habanero.Crossref(mailto=mailto, ua_string="wmk-papers", timeout=20)
        self._throttle = throttle or Throttle(0.34 if mailto else 1.0)

    def search(self, query: str, limit: int, year_from: int | None) -> list[Paper]:
        filters = {"from_pub_date": str(year_from)} if year_from else None
        self._throttle.wait()
        found = self._cr.works(query=query, limit=limit, filter=filters)
        message = found["message"] if isinstance(found, dict) else found[0]["message"]
        return [self._paper(item) for item in message.get("items", [])]

    def get(self, doi: str | None, arxiv_id: str | None) -> Paper:
        wanted = normalize_doi(doi) or (f"10.48550/arxiv.{normalize_arxiv(arxiv_id)}" if arxiv_id else None)
        if not wanted:
            raise NotFound("Crossref looks papers up by DOI")
        self._throttle.wait()
        try:
            found = self._cr.works(ids=wanted)
        except Exception as exc:
            if is_not_found(exc):
                raise NotFound(f"no Crossref record for {wanted}") from exc
            raise
        return self._paper(found["message"])

    @staticmethod
    def _paper(item: dict[str, Any]) -> Paper:
        def first(key: str) -> str | None:
            value = item.get(key) or []
            return value[0] if isinstance(value, list) and value else None

        issued = (item.get("issued") or item.get("published") or {}).get("date-parts") or [[None]]
        doi = normalize_doi(item.get("DOI"))
        return Paper(
            title=clean_text(first("title")) or "",
            authors=[
                Author(
                    name=" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", ""),
                    orcid=normalize_orcid(a.get("ORCID")),
                )
                for a in item.get("author") or []
            ],
            year=issued[0][0] if issued and issued[0] else None,
            venue=first("container-title"),
            doi=doi,
            arxiv=normalize_arxiv(doi) if doi and doi.startswith("10.48550/") else None,
            abstract=clean_text(item.get("abstract")),
            url=item.get("URL"),
            found_in="crossref",
            cited_by=item.get("is-referenced-by-count"),
            references=[d for d in (normalize_doi(r.get("DOI")) for r in item.get("reference") or []) if d],
        )
